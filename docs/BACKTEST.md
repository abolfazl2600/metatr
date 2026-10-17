# Deterministic futures event backtest

`BacktestEngine.run(dataset, strategy_version, contract_spec, cost_scenario,
risk_config, seed)` consumes the quality-gated immutable dataset from #14 and
returns a frozen canonical JSON result. An intact ready report and a sourced
contract-specific calendar matching the economic spec are mandatory. Unknown
calendars, gaps and session carry are unsupported and halt by explicit policy.
No continuous BIP/BIT instrument is synthesized.

Events follow closed bar t → decision/event → pending intent → next eligible
bar t+1 open scenario → fees/netting → t+1 close mark/assumed financing → next
decision. A simulated open fill is only finalized after t+1 has closed because
OHLC/volume participation checks need the completed bar. It is never a same-bar
fill or an assertion about executable open liquidity. Final intents with no next
bar expire without a fabricated fill. Rejected liquidity/price/risk fills are
journaled, never silently replaced with another price.

`SimulationRisk` explicitly supplies initial simulated USD equity, contract size,
notional including fee buffer, and peak drawdown halt. Baseline event mapping is
`IGNORE_SAME_DIRECTION_REVERSE_OPPOSITE`: a first event opens fixed contracts,
a repeated same-direction event is ignored, an opposite event closes/reverses.
`NO_TRADE` leaves positions unchanged. This reviewed execution policy converts
research events to simulated positions; adapters themselves remain event-only.

Futures ledger cash changes by realized PnL, fees and hypothetical financing;
spot principal is never debited. Decimal strings preserve cash, signed contracts,
average entry, realized/unrealized PnL, fees and allocated opening costs. Reversal
fees split between closing and newly opened portions; closed trades include
allocated entry fees/financing as well as exit costs. Marked equity is cash plus
unrealized PnL. Nonpositive equity/drawdown breaches halt and are reported as
`HALTED`; no success/eligibility claim is made for an incomplete simulation.

Outputs include pinned strategy/config, dataset/report/snapshot, contract/cost and
risk versions, seed, decisions, stable event/fill IDs, fills, closed trades,
equity marks and ending ledger. Hashes are stable under the same code/inputs.
Full provenance can change when future dataset bytes change; prior signal actions
and fill economics remain prefix-causal. No broker adapter or order endpoint exists.

Warmup-only prefixes are controlled by `decision_start`. An exclusive
`decision_end < len(bars)` explicitly queues flattening at that boundary's next
bar for fold settlement; failed settlement must be treated as inconclusive by
validation. Flat and buy-hold benchmarks use the same simulation primitive and
cost scenario. A naturally open final position stays marked and is not presented
as a closed trade. Reports and walk-forward selection are separate later modules.
