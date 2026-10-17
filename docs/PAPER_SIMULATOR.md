# Conservative paper execution and reconciliation

`PaperExecutionSimulator` has no exchange client, private endpoint, broker adapter
or MT5 trade dependency. Call `approve` on an explicit NEW intent to run the risk
veto and persist acceptance/rejection. Call `process_next_bar` only with a ready,
closed, exact-market next-bar dataset. The runner supplies frozen registry status,
policy, contract and cost pins; a retired model must stop the upstream runner.

The simulator waits for the bar immediately after the decision bar to close.
It estimates at that next bar's open plus adverse spread/slippage with native tick
rounding, multiplier and taker fees, using the shared Stage 2 economics primitive.
OHLC participation checks are **ex-post hypothetical scenarios**, not evidence of
an executable quote at the open. No partial fill or missing-bar price is invented.
A source/session gap or pending-bar timeout expires the intent and latches HALT.
A late/future/stale bar cannot create a fresh simulated fill.

An accepted order must still have the same approved policy and economics; changed
pins expire it. Before committing, the simulator reconciles the journal, runs a
fresh fill-phase risk assessment, conservatively buffers exposure at the next
bar's high price and checks liquidity/currency/expiry. A risk veto expires the
order and severe failures persist HALT. An unavailable conservative fill cancels
without a fabricated price. Pending orders have bounded lifetime, never accepted
forever by quiet retries.

The fill transaction requires a fresh APPROVE proof with the same policy hash,
finalization UTC and **current ledger hash**. It checks this while holding SQLite's
writer transaction before applying every order/position/cash effect atomically.
A concurrent ledger change invalidates the proof. One writer per account is still
required operationally (the runner provides a process lock). Deterministic fill
IDs/unique intent keys prevent duplicate debits across races/crashes/restarts.

Cash, realized/unrealized PnL, reversal fee allocation, settlement currency and
assumed financing reuse the exact Decimal ledger. Unknown funding/margin rules
remain explicitly hypothetical or unsupported; no real-account eligibility is
implied. HALT blocks **all** new paper fills, including hypothetical exits; open
positions remain visibly marked rather than inventing an emergency close.

Tests compare next-bar prices/fees with the compatible backtest primitive,
hand-check paper cash, retry across reopening, expire skipped bars/changed policies,
and inject source failure. Store tests inject a crash after projection updates
and prove the entire event transaction rolls back with no double debit.
