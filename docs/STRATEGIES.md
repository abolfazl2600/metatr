# Pure research strategies

`metatr.strategies.predict(history, spec, context)` returns an immutable
`StrategyDecision`. It has no exchange client, system clock, random global state,
order API or position state. Context pins the exact five-part Market, final bar
open/close UTC, as-of UTC, snapshot IDs, data SHA-256, lookback and seed.

Pass ascending, unique, timeframe-aligned UTC OHLCV ending at the context bar.
Only closed bars are accepted. Inputs are copied; they are never sorted silently
or modified. Warmup produces `NO_TRADE`; malformed data, retired/tampered specs,
unknown implementations/versions and market mismatches raise `StrategyInputError`.
Callers must catch that error and refuse the decision (NO_TRADE/veto), without
retrying another model or substituting another market. Dataset completeness and
provenance verification remain the historical reader's responsibility.

Both adapters emit **event signals**, not target positions. EMA emits on a crossing;
momentum preserves the legacy repeated direction event when the lookback move
exceeds its threshold. A later execution policy must explicitly map these events
to position changes. `NO_TRADE` does not mean flatten an existing position.

The initial reviewed adapter compatibility map supports registry version `1.0.0`
for `ema_cross` and `momentum`. Other versions fail explicitly until reviewed.
The legacy `futures_core.Candidate` and `select_model` APIs remain available and
share the extracted calculations, preserving their prior research scoring behavior.
That lightweight scoring is not a validated backtest.

## Adding a strategy safely

1. Add a pure calculation in `strategy_signals.py`; no network, clock or mutable globals.
2. Add strict parameter, feature and warmup validation to the registry allowlist.
3. Add an explicit implementation/version entry to `strategies.PLUGINS` in code.
   Never load arbitrary Python paths or expressions from JSON.
4. Add independently calculated golden outputs, prefix/no-lookahead tests, repeated
   replay, invalid-input and exact-market/version isolation tests.
5. Register a new immutable version. Never change the semantics of an existing pin.
6. Document event semantics, required data and unsupported conditions.

All decisions carry `execution=DISABLED` and make no profitability assertion.
