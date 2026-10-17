"""Per-market candidate strategies and conservative, causal walk-forward model selection.

This is a research-only selector, not trained ML or validated profitability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from metatr.data import validate_bars
from metatr.strategy_signals import SIGNALS


@dataclass(frozen=True)
class Market:
    exchange: str
    asset: str
    instrument: str
    product_id: str
    timeframe: str

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.asset}:{self.instrument}:{self.product_id}:{self.timeframe}"


@dataclass(frozen=True)
class Candidate:
    name: str
    parameters: dict

    def predict(self, closes: pd.Series) -> int:
        """-1=short, 0=flat, 1=long; only closes up through prediction time."""
        if self.name == "momentum":
            parameters = {"lookback": int(self.parameters.get("lookback", 8)),
                          "threshold": float(self.parameters.get("threshold", 0.001))}
            if not 1 <= parameters["lookback"] <= 200 or not 0 <= parameters["threshold"] <= 1:
                raise ValueError("Invalid momentum parameters")
        elif self.name == "ema_cross":
            parameters = {"fast": int(self.parameters.get("fast", 12)),
                          "slow": int(self.parameters.get("slow", 26))}
            if not 1 <= parameters["fast"] < parameters["slow"] <= 300:
                raise ValueError("Invalid EMA periods")
        else:
            raise ValueError(f"Unknown model: {self.name}")
        return SIGNALS[self.name](closes, parameters)


def profile_for(market: Market, configuration: dict) -> dict:
    """Exact market profile overrides exchange/asset/instrument/timeframe wildcard."""
    profiles = configuration["profiles"]
    wildcard = f"{market.exchange}:{market.asset}:{market.instrument}:*:{market.timeframe}"
    for key in (market.key, wildcard):
        if key in profiles:
            return profiles[key]
    raise ValueError(f"No model profile for {market.key}; refuse implicit fallback")


def load_configuration(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def select_model(bars: pd.DataFrame, market: Market, configuration: dict) -> dict:
    bars = validate_bars(bars)
    profile = profile_for(market, configuration)
    candidates = [Candidate(item["name"], item.get("parameters", {}))
                  for item in profile["models"]]
    if not candidates:
        raise ValueError("Market profile must provide at least one model")
    window = int(profile.get("evaluation_bars", 80))
    min_trades = int(profile.get("min_trades", 3))
    cost_bps = float(profile.get("round_trip_cost_bps", 10))
    if not 20 <= window <= 200 or min_trades < 1 or not 0 <= cost_bps <= 1000:
        raise ValueError("Invalid evaluation/risk settings")
    if len(bars) < window + 55:
        raise ValueError("Not enough completed futures candles for causal evaluation")
    closes = bars["close"]
    # All backtest decisions use only candles at or before t; PnL t->t+1 is
    # evaluated only through the penultimate bar. Current candle is never scored.
    history = closes.iloc[:-1]
    first = len(history) - window - 1
    ranking: list[dict] = []
    for candidate in candidates:
        pnl: list[float] = []
        for pos in range(first, len(history) - 1):
            action = candidate.predict(history.iloc[:pos + 1])
            if action:
                forward_return = float(history.iloc[pos + 1] / history.iloc[pos] - 1)
                pnl.append(action * forward_return - cost_bps / 10_000)
        avg = sum(pnl) / len(pnl) if pnl else None
        ranking.append({"model": candidate.name, "trades": len(pnl),
                        "mean_net_return": round(avg, 8) if avg is not None else None,
                        "eligible": len(pnl) >= min_trades and avg is not None and avg > 0})
    qualified = [(result["mean_net_return"], index) for index, result in enumerate(ranking)
                 if result["eligible"]]
    winner = max(qualified)[1] if qualified else None
    action = candidates[winner].predict(closes) if winner is not None else 0
    return {"market": market.key, "source": "coinbase_public_futures",
            "timestamp": bars.index[-1].isoformat(), "bars": len(bars),
            "selected_model": candidates[winner].name if winner is not None else None,
            "action": {1: "LONG", -1: "SHORT", 0: "NO_TRADE"}[action],
            "reason": "causal research selection; not live-validated" if winner is not None
                      else "insufficient positive historical evidence; no trade",
            "model_scores": ranking, "execution": "DISABLED", "synthetic": False}
