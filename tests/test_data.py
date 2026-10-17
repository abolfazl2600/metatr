import pandas as pd
import pytest

from metatr.data import sample_bars, validate_bars


def test_sample_is_utc_and_consistent():
    bars = sample_bars(80)
    assert len(bars) == 80
    assert str(bars.index.tz) == "UTC"
    assert (bars["high"] >= bars[["open", "close"]].max(axis=1)).all()


def test_duplicate_candles_rejected():
    bars = sample_bars(60)
    bars.index = pd.DatetimeIndex([bars.index[0]] * len(bars))
    with pytest.raises(ValueError, match="duplicate"):
        validate_bars(bars)


def test_inconsistent_ohlc_rejected():
    bars = sample_bars(60)
    bars.loc[bars.index[-1], "low"] = 9999
    with pytest.raises(ValueError, match="Inconsistent"):
        validate_bars(bars)


def test_naive_timestamps_rejected():
    bars = sample_bars(60)
    bars.index = bars.index.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_bars(bars)
