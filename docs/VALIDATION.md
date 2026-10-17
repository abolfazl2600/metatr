# Chronological walk-forward validation

`plan_folds(dataset, FoldConfig(...), warmup)` supports rolling fixed-length
training and anchored expanding training. Every index is an ordered candle index
inside one exact native product/timeframe. Unknown gaps/session carry and contract
rollover stitching are refused. No random shuffle is implemented.

Parameters: `train_bars`, optional `validation_bars`, `test_bars`, `step_bars`,
`purge_bars`, `embargo_bars`, declared `holding_horizon`, mode, and at least two
independent OOS folds. Step must be at least test length. Both purge and embargo
must cover **max(strategy warmup, holding horizon)**; insufficient or overlapping
splits fail. Training must contain warmup plus three tradable/settlement bars.

Example Python configuration for a sufficiently long validated M15 fixture:

```python
config = FoldConfig(train_bars=80, validation_bars=20, test_bars=30,
                    step_bars=30, purge_bars=28, embargo_bars=28,
                    holding_horizon=4, mode="rolling")
result = walk_forward(dataset, registered_candidates, contract, costs, risk, config, seed=0)
```

`walk_forward` scores **all preset registered candidates** only on training and,
when provided, a separate validation window. It freezes the selected config before
calling the test simulation. Deterministic selection maximizes validation ending
equity (otherwise training ending equity), with config digest as tie breaker.
All tested candidate versions, scores and training/validation results are audited.
There is no dynamic parameter optimizer or trained learner in these baselines.

Test prefixes provide causal warmup only; no decisions/fills occur in the prefix.
Each fold starts from its own simulated initial equity. The final in-fold candle
is reserved for a next-bar settlement queued on the preceding close. Failed
settlement, halted runs, zero test trades and insufficient independent folds are
inconclusive. Positions never silently carry across folds. A declared holding
horizon also enforces timed next-bar exits in the execution policy; it is not
merely a claim in split metadata. Flat and buy-and-hold benchmarks use identical
cost/fill primitives; buy-and-hold stays held until the fold's settlement boundary.

A derived subset preserves the full parent snapshot provenance, although raw
captures can cover a longer interval. Strategy features only see the sliced
candle prefix. Modifying future OOS candles can change source/dataset IDs but
cannot alter prior training scores or the frozen candidate configuration.

Every result stays `research_only=true`, `execution=DISABLED`, `action=NO_TRADE`.
Preset candidate comparison has multiple-testing limitations; neither a training
winner nor positive OOS results prove future profitability. Independent fold
percentages must not be averaged as a portfolio return; reports handle equity
weighting separately.
