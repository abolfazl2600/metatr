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

## Stage 2: continuous verified M15 Paper processing and MT5 handoff

The explicitly opted-in `paper_setup --start --confirm-paper` worker polls the public
Coinbase BIP futures feed every 30 seconds **while the already API-confirmed
session calendar remains valid**. It ingests only validated closed M15 bars and
reconciles each account before processing new checkpoints. An unchanged last bar
creates no duplicate simulated order or new chart signal. Unknown gaps, source
failures and outdated session coverage fail closed. No multi-session renewal is
implemented here (separate #44). The worker runs only in the current foreground
terminal and does not install a persistent macOS daemon.

Pass `--display-output-dir /absolute/FILE_COMMON/root` to the setup worker to
publish the **most recently committed and risk-checked Paper research event** to
MT5. The Paper decision pins a verified per-bar prefix, whereas the strictly
validated MQL5 protocol pins the complete display dataset; the publisher checks
both identities, original model and checkpoint before copying the decision for
visualization. For a risk-vetoed event it displays `NO_TRADE`. Historical
unverified Paper decisions are NOT invented to fill older marker positions.
The manifest always includes `execution=DISABLED` and the marker indicates
`PAPER_SIMULATED_SIGNAL_NOT_ORDER` (research event, NOT a filled order).
Journal fills, pending intents, account equity and risk decisions remain in the
Paper report, not broker charts. Every successful display publication creates
immutable dual-hash evidence in `$WORKSPACE/display-evidence/`. Chart-side
verification and native Wine acceptance remain separate/manual (#20/#21).

Each bounded public start automatically writes an immutable observation into
`$WORKSPACE/observations/` on the **first poll**, whenever a **new M15 bar is
committed**, and when the worker detects a **HALT**; an operational exception
attempts to record one final failure observation. Observations are read-only
SQLite-backed snapshots (see the separate observer section). They do NOT imply
continuous monitoring between samples. Backups remain an explicit operator
choice (`python3 -m metatr.paper_observe --backup`) to avoid making a full copy
every 15 minutes. Observations with stale data, HALT, missing checkpoints or
reconciliation failures are labeled ATTENTION.

To summarize the on-disk observations without inventing service uptime:

```bash
python3 -m metatr.paper_setup --workspace "$HOME/metatr-state/paper-bip" --status
python3 -m metatr.paper_setup --workspace "$HOME/metatr-state/paper-bip" --report
python3 -m metatr.paper_monitor_report --workspace "$HOME/metatr-state/paper-bip"
```

`paper_monitor_report` verifies every immutable evidence hash and exact account
identity, counts real system-clock observations by UTC day, shows alert frequency,
last Paper status, known gaps between observations and explicitly excludes any
`--now-utc` operator override from operational evidence. Counts are **samples,
not a percentage of market uptime**, and the summary always leaves operator
sign-off pending. The output is a content-addressed JSON file in
`$WORKSPACE/monitoring-reports/`.

For recovery, stop the foreground loop, record `--status`, write an explicit
backup and report, review `recent_halt_events`/missing intervals and sourced
calendar, and reconcile the journal. Starting again is never an automatic HALT
reset. Reset only through the existing `paper_cli reset-halt --reason ...`
workflow with the complete pinned files and a fresh, covered data range. The
first public restart does not synthesize unknown historical fills. If the
session has ended, leave the account safely stopped until independently sourced
renewal is supported; don't rewrite JSON calendar files by hand.


## Explicit Coinbase session renewal (Issue #44)

The initial setup is intentionally pinned to the single public session returned
by Coinbase. A stale `source_received_utc` does **not** mean an API key is missing:
public product/candle endpoints need no trading credentials. Stop the foreground
worker with Ctrl+C; do not edit `calendar.json` or `contract.json` by hand.

```bash
WORKSPACE="$HOME/metatr-state/paper-bip"
python3 -m metatr.paper_setup --workspace "$WORKSPACE" --status
python3 -m metatr.paper_setup --workspace "$WORKSPACE" --report
python3 -m metatr.paper_observe --workspace "$WORKSPACE"
python3 -m metatr.paper_setup --workspace "$WORKSPACE" --renew-session
```

Renewal fetches the exact BIP-20DEC30-CDE futures product and 80 public M15 bars.
It requires an API-confirmed OPEN session, unchanged structural contract metadata,
UTC M15 boundaries, complete/fresh in-session bars, a reconciled Paper journal,
unchanged model/risk pins, no unresolved orders, an acceptable current position,
and continuous candle coverage from the last committed checkpoint. It never
guesses future sessions or fabricates bars/fills to bridge a gap.

The new `contract.json` and `calendar.json` are written together into an
immutable `session-generations/<id>/` directory. Before-images of the prior
`setup.json`, effective contract and calendar are preserved by raw SHA-256.
The generation also contains `renewal-<sha256>.json`, public metadata, and a link to the
independent Issue #43 observation evidence. Root `setup.json` is the single
atomic commit pointer; a staged/orphan generation is not active until that
pointer references it. Runner arguments verify the active files' hashes.
Renewal updates market-data cache and evidence only; it does not change Paper
balances, fills, intents, risk policy, journal, or HALT state.

If the result has `paper_halted: false`, review the renewal event and restart:

```bash
python3 -m metatr.paper_setup --workspace "$WORKSPACE" --start --confirm-paper --display-output-dir "$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
```

If `paper_halted: true`, renewal deliberately leaves the kill switch latched.
After reviewing the outage and renewal evidence, use the separate explicit reset.
It fetches fresh public data, checks that the active session still matches the
generation, validates checkpoint continuity and risk, and requires a written
reason of at least eight characters:

```bash
python3 -m metatr.paper_setup --workspace "$WORKSPACE" --reset-halt --reason "Reviewed outage and verified current session and Paper risk health"
```

An unchanged session boundary is valid when Coinbase still reports the session
open: renewal can refresh candles within that same session. The command records
`UNCHANGED_SESSION_REFRESH` versus `NEW_SESSION_VERSION` in its evidence.
Freshness, candle completeness, checkpoint continuity, risk, and contract checks
remain mandatory; unchanged session metadata never permits stale candles.

If renewal reports `PUBLIC_SESSION_CLOSED`, `PUBLIC_DATA_NOT_READY`,
`UNOBSERVED_CHECKPOINT_GAP`, a risk-gate failure, unknown order state or a
contract pin mismatch, do not bypass the check. Leave the account stopped and
review the evidence and logs. A failed renewal neither resets HALT nor changes
the active setup pointer.

This is a real public API smoke check, not an offline fixture. It uses no API key
or private endpoint. CI verifies offline safety gates; the current-session
response and multi-day continuity still require operator observation. It does
not establish 14-day acceptance or profitability.
