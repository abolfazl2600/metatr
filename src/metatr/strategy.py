"""A transparent EMA-cross reference strategy, NOT a validated trading system."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from metatr.data import validate_bars


@dataclass(frozen=True)
class Signal:
    symbol: str
    timeframe: str
    source: str
    timestamp: str
    action: str
    reason: str
    fast_ema: float
    slow_ema: float
    synthetic: bool

    def as_dict(self) -> dict:
        return asdict(self)


def ema_cross_signal(
    bars: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    source: str,
    fast: int = 20,
    slow: int = 50,
) -> Signal:
    """Emit BUY/SELL only on a fresh crossover of two completed candles."""
    if not 1 <= fast < slow:
        raise ValueError("EMA periods must satisfy 1 <= fast < slow")
    bars = validate_bars(bars)
    if len(bars) < slow + 2:
        raise ValueError(f"At least {slow + 2} completed candles required")
    close = bars["close"]
    a = close.ewm(span=fast, adjust=False, min_periods=slow).mean()
    b = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    previous_diff = a.iloc[-2] - b.iloc[-2]
    latest_diff = a.iloc[-1] - b.iloc[-1]
    if previous_diff <= 0 < latest_diff:
        action, reason = "BUY", "EMA fast crossed above EMA slow on a closed candle"
    elif previous_diff >= 0 > latest_diff:
        action, reason = "SELL", "EMA fast crossed below EMA slow on a closed candle"
    else:
        action, reason = "NO_TRADE", "No confirmed EMA crossover on latest closed candle"
    return Signal(
        symbol=symbol,
        timeframe=timeframe,
        source=source,
        timestamp=bars.index[-1].isoformat(),
        action=action,
        reason=reason,
        fast_ema=round(float(a.iloc[-1]), 8),
        slow_ema=round(float(b.iloc[-1]), 8),
        synthetic=source == "sample",
    )
