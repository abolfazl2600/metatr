# Paper-only operation and recovery runbook

The CLI is a **simulated account**, not real-money execution. There is no live
switch, exchange credential, private endpoint, broker adapter or MT5 trade route.
Importing modules and running the legacy futures CLI never starts a paper worker.
`run` and `replay` require both `--mode paper --confirm-paper`; no account is
created automatically. `metatr-paper` is also available after reinstalling.

## Configuration and explicit startup

Use separate account IDs and SQLite files for independent experiments where useful.
Every account binds one complete Market and chooses either REPLAY or PUBLIC mode;
never silently switch an existing historical account into a public-data worker.

```bash
git pull origin main
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m metatr.paper_cli init --paper-db data/paper/paper.sqlite \
  --account btc-paper --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --initial-equity 1000
```

Prepare explicit JSON inputs as documented in CONTRACT_ECONOMICS.md and PAPER_RISK.md:
full sourced native ContractSpec, CostScenario with written hypothetical fees,
volume and funding assumptions, exact contract SessionCalendar with current/future
source coverage, and PaperRiskPolicy for this account/initial USD equity. Market
fields in JSON are full five-part strings, quantities/money are decimal strings.
Do not copy generic spot fees, BIP pseudo-expiry, unverified margin or guessed
weekends into these inputs. Missing/unknown economics/calendar => pause/HALT.
Register the exact allowed strategy version separately, never a wildcard.

```bash
python -m metatr.paper_cli run --mode paper --confirm-paper \
  --paper-db data/paper/paper.sqlite --account btc-paper \
  --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --store data/raw/futures.sqlite --registry data/research/registry.sqlite \
  --strategy-id momentum --strategy-version 1.0.0 --contracts 1 \
  --risk-policy path/to/paper-risk.json --contract-spec path/to/contract.json \
  --cost-scenario path/to/costs.json --calendar path/to/calendar.json \
  --count 80 --poll-seconds 30 --max-catch-up-bars 80 \
  --audit-log data/paper/btc-paper.jsonl
```

Only verified public Coinbase closed candles are fetched. Source retries are
bounded to three reads with exponential backoff; 401/403/404 stop immediately,
429 respects a known bounded Retry-After (long/unknown cooldown stops), exhausted
timeouts/429/5xx halt. No order endpoints are called. First PUBLIC startup uses
history for warmup and starts decisions at the latest closed candle, never inventing
historical public-mode fills. `--max-cycles N` permits a bounded operator smoke.
Ctrl-C/SIGTERM stops polling gracefully. An OS account file lock prevents a second
worker; it releases on process death. A duplicate worker is rejected without
resetting or halting the healthy worker's account. Local POSIX filesystems are
required for worker locks/SQLite; no shared NFS worker deployment is supported.

## Offline replay

Use a **different account**, initialized explicitly, and pin all local store/registry
and economics/calendar files. No network calls occur in replay.

```bash
python -m metatr.paper_cli replay --mode paper --confirm-paper \
  --paper-db data/paper/replay.sqlite --account btc-replay \
  --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --store data/raw/futures.sqlite --registry data/research/registry.sqlite \
  --strategy-id momentum --strategy-version 1.0.0 --contracts 1 \
  --risk-policy path/to/replay-risk.json --contract-spec path/to/contract.json \
  --cost-scenario path/to/costs.json --calendar path/to/calendar.json \
  --start-utc 2026-10-16T00:00:00Z --end-utc 2026-10-17T00:00:00Z \
  --asof-utc 2026-10-17T00:05:00Z --max-catch-up-bars 350
```

Dates/counts above illustrate syntax; the requested data must actually exist and
pass readiness. Replay uses the closed-bar simulation clock and explicitly permits
later-collected backfill provenance; it does not claim historical live availability.
The fixed event mapping ignores repeated same-direction events and reverses on
opposite events. NO_TRADE does not flatten. Every fill finalizes only after the
next eligible bar closes. Pending orders time out/expire rather than filling
across unknown gaps. Positions have no invented margin/liquidation closeout.

## Status, stop and explicit reset

```bash
python -m metatr.paper_cli status --paper-db data/paper/paper.sqlite \
  --account btc-paper --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15
python -m metatr.paper_cli halt --paper-db data/paper/paper.sqlite \
  --account btc-paper --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --reason 'Operator maintenance'
```

Status/report clearly label PAPER_ONLY_SIMULATED and include opening/current marked
equity, cash, net/realized/unrealized PnL, fees/assumed financing, open contracts,
exposure/drawdown, accepted/rejected/pending intents, closed trade count, last
committed candle/stale age, model/contract/cost/data pins and recent risk reasons.
Reported equity is at the last **committed** mark; compare its timestamp and any
newer health veto before interpreting stale or halted values. No displayed balance
is a real account balance.

`reset-halt` is a separate operator command requiring `--reason`, the full data/
registry/economic/policy/calendar arguments, explicit bounded UTC data range and
`--clock-mode PUBLIC` or `REPLAY`. A historical reset additionally requires
`--now-utc` as explicit simulation time. It reconciles the journal and reruns
fresh source/session/loss/drawdown checks. Continuing breaches refuse reset;
restart alone never clears HALT. Stop the active worker before resetting.

## Recovery, logs and backups

Each bar resumes in order: data/risk health → prior accepted next-bar fills →
assumed financing → mark → pinned signal → risk-approved intent → checkpoint.
Each stage is independently idempotent. Reopen reconstructs/compares every
projection. A crash before checkpoint replays the unfinished bar; already committed
cash effects and fills are reused. Added consistent source captures may add run
provenance, but existing per-bar money and intent pins remain immutable. Changed
price/economics during a partial bar, missing catch-up data, clock rollback,
retired models or excessive backlog halt for review. Public catch-up remains
subject to source freshness; old unknown intervals are not quietly traded.

SQLite lock/disk-full/log-write errors stop the worker. If storage cannot persist
HALT, output explicitly says `PAPER_STOPPED_HALT_PERSIST_FAILED`/halt_persisted=false;
there is no claim that an unavailable DB saved a kill switch. Restore space/storage,
back up and reconcile before restart. Do not manually edit balances/orders or delete
journal rows to fix a mismatch. JSONL audit fields are allowlisted; no credentials,
headers or raw private data are logged. Correlation IDs join bar/risk/run records.
Stop the worker before rotating JSONL logs; archive by date and restart explicitly.

Use SQLite's backup API separately for market, registry and paper DBs, for example:

```python
import sqlite3
with sqlite3.connect("data/paper/paper.sqlite") as source:
    with sqlite3.connect("data/paper/paper-backup.sqlite") as destination:
        source.backup(destination)
```

Never copy only a live main SQLite file while ignoring WAL. `report --output DIR`
exports a content-addressed immutable JSON audit including journal/ledger/status.
Use the same account/market/paper-db flags. Protect simulated financial reports
and logs with normal local file permissions.

Optional `--display-output-dir` publishes only one-way PAPER_SIMULATED-marked
research/health signals via the outbound protocol; simulated fills remain in the
paper journal/report. It never enables the native viewer to execute orders.
Actual Mac/Wine visualization still has its separate #20/#21 acceptance gate.

## Operational acceptance — PENDING, not established by CI

Offline CI proves deterministic fixture replay, idempotence, failure injection,
locks, risk halts and accounting. It **does not** prove continuous public-feed
availability or native Wine rendering. No monitored paper soak has been run here.
Record a separate operator sign-off after an initial suggested **14-day** observed
paper period: exact versions, source availability/stale ages, every outage/reset,
restart reconciliation, ledger conservation, pending order expiry, drawdown/loss
latches and any display evidence. A halt must be investigated, not automatically
cleared. Green tests or a positive simulated PnL are not profitability evidence.

Issue #25's implementation can merge after CI, but operational sign-off remains
open until this evidence exists. Any future real-money development requires a
new separately approved issue covering eligibility, authenticated execution,
live reconciliation and hard risk controls. There is no real execution option here.

## Immutable point-in-time observation for monitored Paper acceptance

After preparing the public scenario, `paper_observe` records an independent, strictly
**read-only local** observation of the Paper journal and last-known public source.
It never fetches Coinbase data, starts polling, resets HALT, or claims that a
historical record proves 14 days of monitoring. Each call copies the Paper SQLite
file through the WAL-aware SQLite backup API, reconciles its hash-chained journal
against that snapshot and saves a content-addressed JSON evidence file.

```bash
python3 -m metatr.paper_observe --workspace "$HOME/metatr-state/paper-bip" \
  --backup --require-healthy
```

The evidence is written to `$HOME/metatr-state/paper-bip/observations/<sha256>.json`.
With `--backup`, independently consistent Paper, market and registry SQLite files
are retained in `observations/backups/`, named by their SHA-256. This is **not**
a cross-database atomic snapshot: separate databases may represent slightly
different instants while the worker operates. Backup retention is manual; do not
commit these local financial/provenance databases to Git.

The evidence includes wall-clock observation UTC, journal head/event count,
reconciliation outcome, last closed bar, source age, Paper equity/positions,
HALT state, session coverage boundary, and explicit alert codes. `--require-healthy`
returns status 2 for any alert, without changing the Paper account. A newly
prepared but not-yet-run account correctly reports missing checkpoints/receipts.
Use one command per observed interval, record any downtime, and investigate
alerts before resuming. `--now-utc` is a deterministic operator/testing override;
such evidence is labeled `OPERATOR_OVERRIDE` and is **not** proof of real-time
monitoring. Missing or corrupt Paper DBs produce an unsafe observation rather
than a false success. The system cannot retroactively create past observations.

For operational sign-off, collect observations over the agreed period along
with operator incident reviews and native screenshots as separate evidence.
Even a set of healthy snapshots does not prove continuous uptime, actual trade
execution or strategy profitability.
