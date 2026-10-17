from dataclasses import replace

import pandas as pd
import pytest

from metatr.data import sample_bars
from metatr.futures_core import Candidate, Market
from metatr.model_registry import StrategySpec
from metatr.strategies import StrategyContext, StrategyInputError, predict

MARKET = Market("coinbase", "BTC", "FUTURE", "BIP-20DEC30-CDE", "M15")


def setup(name="ema_cross", count=100):
    params = {"fast": 12, "slow": 26} if name == "ema_cross" else {
        "lookback": 8, "threshold": 0.001}
    spec = StrategySpec.create(MARKET, name, "1.0.0", name, params,
                               created_at="2020-01-01T00:00:00Z")
    bars = sample_bars(count)
    closed = bars.index[-1] + pd.Timedelta(minutes=15)
    ctx = StrategyContext(MARKET, bars.index[-1], closed, closed,
                          ("a" * 64,), "b" * 64, spec.warmup_bars)
    return bars, spec, ctx


@pytest.mark.parametrize("name", ["ema_cross", "momentum"])
@pytest.mark.parametrize("shape", ["wave", "rising", "falling", "flat"])
@pytest.mark.parametrize("count", [1, 8, 27, 28, 100])
def test_golden_legacy_and_repeat(name, shape, count):
    bars, spec, ctx = setup(name, count)
    if shape != "wave":
        values = {"rising": [100 + i for i in range(count)],
                  "falling": [200 - i for i in range(count)], "flat": [100] * count}[shape]
        for col in ("open", "high", "low", "close"):
            bars[col] = values
    before = bars.copy(deep=True)
    decision = predict(bars, spec, ctx)
    expected = Candidate(name, spec.parameters).predict(bars.close)
    assert decision.action == {1: "LONG", -1: "SHORT", 0: "NO_TRADE"}[expected]
    assert decision == predict(bars, spec, ctx)
    assert decision.semantics == "event_signal"
    pd.testing.assert_frame_equal(bars, before)


@pytest.mark.parametrize("name", ["ema_cross", "momentum"])
def test_prefix_and_future_rejection(name):
    bars, spec, ctx = setup(name)
    extended = sample_bars(110)
    expected = predict(extended.iloc[:100], spec, ctx)
    extended.loc[extended.index[100:], "close"] = 999
    assert predict(extended.iloc[:100], spec, ctx) == expected
    with pytest.raises(StrategyInputError):
        predict(extended, spec, ctx)


@pytest.mark.parametrize("kind", ["reverse", "duplicate", "naive", "nan", "infinite",
                                  "volume", "ohlc", "unaligned", "feature"])
def test_invalid_history(kind):
    bars, spec, ctx = setup()
    if kind == "reverse":
        bars = bars.iloc[::-1]
    elif kind == "duplicate":
        bars = pd.concat([bars, bars.iloc[-1:]])
    elif kind == "naive":
        bars.index = bars.index.tz_localize(None)
    elif kind == "unaligned":
        bars.index = bars.index + pd.Timedelta(seconds=1)
    elif kind == "feature":
        bars = bars.drop(columns="volume")
    else:
        col, value = {"nan": ("close", float("nan")),
                      "infinite": ("close", float("inf")), "volume": ("volume", -1),
                      "ohlc": ("high", 1)}[kind]
        bars.loc[bars.index[-1], col] = value
    with pytest.raises(StrategyInputError):
        predict(bars, spec, ctx)


@pytest.mark.parametrize("kind", ["market", "version", "params", "digest", "retired",
                                  "open", "provenance", "lookback"])
def test_invalid_pins_and_context(kind):
    bars, spec, ctx = setup()
    if kind == "market":
        ctx = replace(ctx, market=replace(MARKET, product_id="BIT-26DEC26-CDE"))
    elif kind == "open":
        ctx = replace(ctx, asof_utc=ctx.bar_open_utc)
    elif kind == "provenance":
        ctx = replace(ctx, snapshot_ids=())
    elif kind == "lookback":
        ctx = replace(ctx, required_lookback=1)
    else:
        spec = replace(spec, **{"version": {"version": "2.0.0"},
                                "params": {"parameters_json": '{"fast":26,"slow":12}'},
                                "digest": {"config_sha256": "c" * 64},
                                "retired": {"status": "retired"}}[kind])
    with pytest.raises(StrategyInputError):
        predict(bars, spec, ctx)


@pytest.mark.parametrize("name", ["ema_cross", "momentum"])
@pytest.mark.parametrize("direction", [1, -1])
def test_independent_golden_event(name, direction):
    bars, spec, ctx = setup(name, 30)
    for col in ("open", "high", "low", "close"):
        bars[col] = [100] * 29 + [100 + direction * 10]
    decision = predict(bars, spec, ctx)
    assert decision.action == {1: "LONG", -1: "SHORT"}[direction]
    assert decision.config_sha256 == spec.config_sha256
    assert decision.timestamp == ctx.bar_close_utc.isoformat()
