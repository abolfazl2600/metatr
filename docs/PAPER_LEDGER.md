# Isolated event-sourced paper ledger

`metatr.paper.store.PaperStore` is explicit, simulated state in a separate
`data/paper/paper.sqlite` (application ID MTRP, schema v1). Importing creates no
account, run, order or worker. Initialize an account explicitly with a positive
USD decimal-string opening equity and one **complete Market**. An account is
bound to one native product/timeframe; use separate account IDs for independent
markets. No FX conversion, real balances, credentials or order endpoints exist.

The schema contains paper_accounts, paper_runs, intents, paper_orders, paper_fills,
positions, cash_ledger, equity_marks, risk_events, kill_switch_events and audit_events.
All financial values are exact decimal strings. SQLite uses foreign keys, WAL,
5-second busy timeout and BEGIN IMMEDIATE for one writer per event transaction.
Unknown schemas/application IDs or pre-existing unrelated DBs are refused.

Every journal event and **all** ledger/order/position/cash/risk projections commit
atomically. Event IDs are deterministic; repeats are idempotent only for identical
content. The per-account journal hash chain and UPDATE/DELETE refusal make events
append-only through the API. This detects accidental corruption and unauthorized
partial edits, not a cryptographically authenticated adversary replacing the whole
DB and chain. Back up the DB/WAL using SQLite's backup facility, not partial file copies.

`reconcile(account)` rebuilds projections in a separate in-memory DB from the
hash-checked journal, compares every materialized table and refuses mismatches.
It never silently repairs, adjusts money or invents fills. A runner must reconcile
before resuming; mismatch requires halt/review. Crashes during any projection step
roll back the journal and all effects together. Retrying cannot debit twice.

Run pins contain exact market, immutable strategy config/version, dataset digest,
contract spec/native metadata and explicit cost scenario/fingerprint. Intents use
an account + full Market + strategy config + closed UTC bar + intent type key.
A unique per-bar constraint rejects contradictory direction/size attempts and
prevents replays/new run IDs from producing duplicate orders. The first observed
provenance remains pinned. The account owns every run/order/fill.

State machine: `NEW → ACCEPTED | REJECTED`; accepted orders can only become
`FILLED | CANCELLED | EXPIRED`. Only the dedicated fill event can set FILLED.
Acceptance requires an APPROVE record and a nonhalted account; the next risk
module supplies enforceable policy decisions. Fills must match approved direction,
size, contract/cost pins and next-bar timing, and finalize only after that bar
closes. The store itself never fetches prices or fabricates a fill.

Accounting reuses the tested exact futures netting/reversal primitives: no spot
principal debit, fee split across close/open portions, realized/unrealized PnL,
allocated entry costs and separately journaled assumed financing. Cash ledger
amounts, position ledger and equity marks reconcile to the same opening capital.
Zero fee/funding remains explicit rather than implying verified venue economics.

A manual HALT latches persistently. Reset is a separate event with reason and fresh
health attestation; process restart never resets it. Higher-level risk/runner APIs
must supply actual fresh health checks and gate acceptance, not merely set a bool.
Offline tests cover exact reversals, duplicates, reopen reconciliation, rollback
fault injection, illegal transitions, account/timeframe isolation and tamper/schema refusal.
