"""Deterministic baseline event calculations shared with the legacy selector."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

import pandas as pd


def ema_cross(closes: pd.Series, parameters: dict) -> int:
    fast, slow = parameters["fast"], parameters["slow"]
    if len(closes) < slow + 2:
        return 0
    left = closes.ewm(span=fast, adjust=False).mean()
    right = closes.ewm(span=slow, adjust=False).mean()
    before = float(left.iloc[-2] - right.iloc[-2])
    after = float(left.iloc[-1] - right.iloc[-1])
    return 1 if before <= 0 < after else -1 if before >= 0 > after else 0


def momentum(closes: pd.Series, parameters: dict) -> int:
    lookback, threshold = parameters["lookback"], parameters["threshold"]
    if len(closes) <= lookback:
        return 0
    move = float(closes.iloc[-1] / closes.iloc[-1 - lookback] - 1)
    return (move > threshold) - (move < -threshold)


# Adding a plugin requires a reviewed code change, never a config import or eval.
SIGNALS: Mapping[str, Callable[[pd.Series, dict], int]] = MappingProxyType({
    "ema_cross": ema_cross, "momentum": momentum,
})
