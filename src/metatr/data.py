"""Read-only OHLCV inputs with strict timestamps and price validation."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED = ("open", "high", "low", "close")
TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}


def validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a UTC-sorted copy of closed bars; raise on malformed inputs."""
    if frame.empty:
        raise ValueError("No candles received")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Candle index must contain timestamps")
    if frame.index.tz is None:
        raise ValueError("Timestamps must be timezone-aware (UTC)")
    if frame.index.hasnans or frame.index.has_duplicates:
        raise ValueError("Missing or duplicate candle timestamps")
    missing = set(REQUIRED) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")
    out = frame.copy().sort_index()
    out.index = out.index.tz_convert("UTC")
    for key in REQUIRED:
        out[key] = pd.to_numeric(out[key], errors="raise")
    prices = out.loc[:, list(REQUIRED)].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Prices must be positive and finite")
    if (
        (out["high"] < out[["open", "close", "low"]].max(axis=1)).any()
        or (out["low"] > out[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise ValueError("Inconsistent OHLC values")
    if "volume" in out.columns:
        out["volume"] = pd.to_numeric(out["volume"], errors="raise")
        volume = out["volume"].to_numpy(dtype=float)
        if not np.isfinite(volume).all() or (volume < 0).any():
            raise ValueError("Volume must be non-negative and finite")
    return out


def sample_bars(count: int = 300) -> pd.DataFrame:
    """Deterministic, *synthetic* candles for offline development only."""
    if count < 1:
        raise ValueError("count must be positive")
    t = np.arange(count, dtype=float)
    close = 100 + 0.015 * t + np.sin(t / 7.0) * 0.8
    previous = np.concatenate(([close[0]], close[:-1]))
    idx = pd.date_range("2020-01-01", periods=count, freq="15min", tz="UTC")
    return validate_bars(
        pd.DataFrame(
            {
                "open": previous,
                "high": np.maximum(previous, close) + 0.07,
                "low": np.minimum(previous, close) - 0.07,
                "close": close,
                "volume": np.full(count, 100, dtype=int),
            },
            index=idx,
        )
    )


def csv_bars(filepath: str | Path) -> pd.DataFrame:
    """Read candle-close-only CSV; source is responsible for dropping live bars."""
    frame = pd.read_csv(filepath)
    if "timestamp" not in frame.columns:
        raise ValueError("CSV requires timestamp column")
    # Reject naive timestamps rather than silently assuming local timezone.
    if frame["timestamp"].astype(str).str.fullmatch(r"\d+(\.\d+)?").all():
        stamps = pd.to_datetime(pd.to_numeric(frame["timestamp"]), unit="s", utc=True)
    else:
        raw = frame["timestamp"].astype(str)
        if not raw.str.contains(r"(Z|[+-]\d{2}:?\d{2})$", regex=True, case=False).all():
            raise ValueError("CSV timestamps require Z, timezone offset, or Unix seconds")
        stamps = pd.to_datetime(raw, utc=True, errors="raise")
    frame = frame.drop(columns="timestamp")
    frame.index = pd.DatetimeIndex(stamps)
    return validate_bars(frame)


def mt5_bars(symbol: str, timeframe: str, count: int = 300) -> pd.DataFrame:
    """Fetch completed bars only; requires MT5 terminal + Python on Windows x64."""
    if sys.platform != "win32":
        raise RuntimeError(
            "Official MetaTrader5 Python integration needs Windows x64. "
            "Use --source sample/csv on macOS, or run this command on Windows."
        )
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if count < 2:
        raise ValueError("count must be at least 2")
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("Install Windows MT5 dependency: pip install -e '.[mt5]'") from exc
    mapping = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol unavailable: {symbol}; {mt5.last_error()}")
        # Index 0 is the still-forming candle; position 1 is the latest *closed* candle.
        bars = mt5.copy_rates_from_pos(symbol, mapping[timeframe], 1, count)
        if bars is None or len(bars) == 0:
            raise RuntimeError(f"MT5 returned no closed bars: {mt5.last_error()}")
        frame = pd.DataFrame(bars)
        frame.index = pd.to_datetime(frame.pop("time"), unit="s", utc=True)
        frame = frame.rename(columns={"tick_volume": "volume"})
        return validate_bars(frame)
    finally:
        mt5.shutdown()
