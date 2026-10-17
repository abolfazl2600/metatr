import pytest

from metatr.data import sample_bars, validate_bars
from metatr.strategy import ema_cross_signal


def _prices(closes):
    bars = sample_bars(len(closes))
    bars["close"] = closes
    bars["open"] = closes
    bars["high"] = [x + 0.1 for x in closes]
    bars["low"] = [x - 0.1 for x in closes]
    return validate_bars(bars)


def _signal(closes):
    return ema_cross_signal(
        _prices(closes), symbol="TEST", timeframe="M15", source="sample", fast=2, slow=4
    )


def test_buy_on_last_closed_candle():
    out = _signal([10.0] * 12 + [20.0])
    assert out.action == "BUY"
    assert out.synthetic is True


def test_sell_on_last_closed_candle():
    out = _signal([20.0] * 12 + [10.0])
    assert out.action == "SELL"


def test_no_trade_without_new_cross():
    assert _signal([10.0] * 13).action == "NO_TRADE"


def test_insufficient_history():
    with pytest.raises(ValueError, match="At least"):
        _signal([10.0] * 4)


def test_invalid_period_order():
    with pytest.raises(ValueError, match="periods"):
        ema_cross_signal(
            sample_bars(), symbol="TEST", timeframe="M15", source="sample", fast=50, slow=20
        )
