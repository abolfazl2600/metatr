"""Pure pinned strategy decisions: no clients, clocks, orders or hidden state."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

import pandas as pd

from metatr.data import validate_bars
from metatr.futures_core import Market
from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.model_registry import StrategySpec
from metatr.strategy_signals import SIGNALS


class StrategyInputError(ValueError):
    """Caller must refuse this research decision; never substitute a strategy."""


def utc(value: datetime) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise StrategyInputError("Explicit UTC timestamp required")
    return stamp


@dataclass(frozen=True)
class StrategyContext:
    market: Market
    bar_open_utc: datetime
    bar_close_utc: datetime
    asof_utc: datetime
    snapshot_ids: tuple[str, ...]
    data_sha256: str
    required_lookback: int
    seed: int = 0


@dataclass(frozen=True)
class StrategyDecision:
    market: str
    action: str
    strategy_id: str
    strategy_version: str
    config_sha256: str
    reason: str
    features_available: tuple[str, ...]
    timestamp: str
    bar_open_utc: str
    data_sha256: str
    snapshot_ids: tuple[str, ...]
    semantics: str = "event_signal"
    execution: str = "DISABLED"

    def to_dict(self) -> dict:
        return asdict(self)


# Implementation ABI versions, distinct from an arbitrary user-assigned model version.
# Registry versions are explicit: new semantic versions require reviewed compatibility here.
PLUGINS = MappingProxyType({(name, "1.0.0"): func for name, func in SIGNALS.items()})


def predict(history: pd.DataFrame, spec: StrategySpec,
            context: StrategyContext) -> StrategyDecision:
    """Use supplied history through the final closed bar only. Invalid input raises.

    A valid short history returns NO_TRADE with an explicit warmup reason. The caller
    applies its conservative failure policy to exceptions. Momentum retains legacy
    repeated directional events; neither adapter defines a persistent position target.
    """
    try:
        rebuilt = StrategySpec.create(
            spec.market, spec.strategy_id, spec.version, spec.implementation_key,
            spec.parameters, features=spec.features, warmup_bars=spec.warmup_bars,
            evaluation=spec.evaluation, created_at=spec.created_at, status=spec.status,
        )
        if rebuilt != spec or spec.status == "retired":
            raise StrategyInputError("Invalid or retired pinned strategy")
        plugin = PLUGINS.get((spec.implementation_key, spec.version))
        if plugin is None:
            raise StrategyInputError("Unsupported implementation/version")
        if spec.market != context.market:
            raise StrategyInputError("Strategy/context market mismatch")
        if (type(context.seed) is not int or type(context.required_lookback) is not int
                or context.required_lookback < spec.warmup_bars):
            raise StrategyInputError("Invalid seed or required lookback")
        if (not isinstance(context.snapshot_ids, tuple) or not context.snapshot_ids
                or any(not isinstance(s, str) or not re.fullmatch(r"[0-9a-f]{64}", s)
                       for s in (*context.snapshot_ids, context.data_sha256))):
            raise StrategyInputError("Snapshot IDs and data SHA-256 provenance required")
        opened, closed, asof = map(utc, (context.bar_open_utc, context.bar_close_utc,
                                       context.asof_utc))
        seconds = TIMEFRAME_SECONDS[context.market.timeframe]
        if closed != opened + pd.Timedelta(seconds=seconds) or closed > asof:
            raise StrategyInputError("Unclosed or inconsistent decision candle")
        if (not isinstance(history.index, pd.DatetimeIndex) or history.index.tz is None
                or str(history.index.tz) != "UTC" or not history.index.is_monotonic_increasing):
            raise StrategyInputError("History must be ascending UTC")
        checked = validate_bars(history)
        if checked.index[-1] != opened:
            raise StrategyInputError("History must end at the context bar, without future candles")
        if (checked.index.asi8 % (seconds * 1_000_000_000) != 0).any():
            raise StrategyInputError("History is not timeframe aligned")
        if set(spec.features) - set(checked.columns) or "volume" not in checked:
            raise StrategyInputError("Missing required OHLCV features")
        enough = len(checked) >= context.required_lookback
        signal = plugin(checked["close"], spec.parameters) if enough else 0
        reason = ("baseline event signal" if signal else "no baseline event") if enough else (
            "insufficient warmup history")
        return StrategyDecision(
            context.market.key, {1: "LONG", -1: "SHORT", 0: "NO_TRADE"}[signal],
            spec.strategy_id, spec.version, spec.config_sha256, reason,
            tuple(sorted(checked.columns.intersection(spec.features))), closed.isoformat(),
            opened.isoformat(), context.data_sha256, context.snapshot_ids,
        )
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        if isinstance(exc, StrategyInputError):
            raise
        raise StrategyInputError(str(exc)) from exc
