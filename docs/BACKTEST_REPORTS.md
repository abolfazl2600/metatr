# Auditable offline reports

`build_report` pins a verified walk-forward result to dataset/snapshots, exact
strategy versions, contract/cost/calendar specifications, risk, fold boundaries,
seed, code revision and Python/package versions. It embeds the event/fill/trade/
equity journals and complete candidate audit, preserving training, validation and
OOS results separately. This is hypothetical research: `action=NO_TRADE`,
`execution=DISABLED`, `backtest_validated=false` remains explicit.

Metrics use **net equity**, starting from initial simulated capital and including
fees/assumed funding and marked open positions. Sharpe uses sample return standard
deviation and zero risk-free rate; Sortino uses root-mean-square negative returns
with zero minimum acceptable return. Both use explicit `annual_bars`, which must
be justified by the sourced session calendar rather than guessed from spot crypto.
Zero volatility/downside, no trades, nonpositive equity and halted/incomplete
runs have N/A ratios or inconclusive status. Max drawdown includes initial equity;
duration counts underwater bars. Profit factor uses closed net trades and is N/A
without losses. Average trade, win/loss distribution, exposure, turnover, fee
burden and flat/buy-hold deltas are recorded with their explicit denominators.

Each OOS fold has independent initial capital. Aggregate net return is **sum of
net PnL / sum of initial fold equity**, not a blind average of percentages or a
fictional compounded portfolio. Aggregate Sharpe/Sortino are N/A because splicing
independent account resets into one equity curve would misstate time weighting.
Per-fold ratios and boundaries remain available.

Cost stress reuses **the same frozen primary selected model and exact OOS fold**,
never reselecting candidates using stressed test results. Each supplied scenario
must have no smaller taker/spread/slippage/funding assumptions and no larger
participation cap than the primary scenario. Explicit OHLC, liquidity, funding,
margin/liquidation and multiple-testing limitations accompany every report.

## CLI

Provide JSON bodies matching the documented dataclasses in `contract_economics`,
`SimulationRisk`, `SessionCalendar` and `FoldConfig`. Market fields in file inputs
are complete colon-separated strings; monetary inputs are decimal strings.
All inputs must describe the actual sourced contract or explicitly synthetic
fixtures; no production economics/calendar is fabricated for convenience.

```bash
python -m metatr.backtest_cli \
  --store data/raw/futures.sqlite --registry data/research/registry.sqlite \
  --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --strategy ema_cross@1.0.0 --strategy momentum@1.0.0 \
  --start-utc 2026-10-01T00:00:00Z --end-utc 2026-10-17T00:00:00Z \
  --asof-utc 2026-10-17T00:05:00Z --seed 0 \
  --contract-spec path/to/contract.json --cost-scenario path/to/costs.json \
  --risk-policy path/to/risk.json --fold-config path/to/folds.json \
  --calendar path/to/calendar.json --annual-bars 24000 \
  --stress-scenario path/to/higher-costs.json --output data/research/backtests
```

The annual frequency above is illustrative, not verified Coinbase session metadata.
The runner is always offline. Insufficient data or invalid provenance cannot
produce a success report. An inconclusive report is still exportable for audit.

`export_report` atomically publishes a completed directory `<output>/<run_id>/`
containing canonical `manifest.json`, OOS `trades.csv` and `equity.csv`. CSV hashes
are pinned in the manifest; output is deterministic for the same code/runtime/
inputs, fsynced before rename, idempotent on exact replay and never overwrites a
completed conflicting report. Temporary directories are cleaned after failure.
Golden offline tests independently check equity/drawdown/cost/ratio edge cases
and exercise actual storage + registry + CLI with synthetic fixtures.

Positive backtest results cannot guarantee live profitability.
