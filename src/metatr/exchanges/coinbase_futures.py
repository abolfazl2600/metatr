"""Coinbase Advanced Trade public US futures data; never routes orders."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from metatr.data import validate_bars

BASE = "https://api.coinbase.com/api/v3/brokerage/market"
GRANULARITIES = {"M1": ("ONE_MINUTE", 60), "M5": ("FIVE_MINUTE", 300),
                 "M15": ("FIFTEEN_MINUTE", 900), "H1": ("ONE_HOUR", 3600),
                 "H4": ("FOUR_HOUR", 14400), "D1": ("ONE_DAY", 86400)}


def _public_get(path: str, params: dict) -> dict:
    query = urlencode(params)
    request = Request(f"{BASE}{path}?{query}", headers={"User-Agent": "metatr/0.2"})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def _is_bitcoin_future(product: dict) -> bool:
    if product.get("product_type") != "FUTURE" or not isinstance(
        product.get("future_product_details"), dict
    ):
        return False
    fields = ("product_id", "display_name", "base_currency_id", "base_display_symbol")
    details = product["future_product_details"]
    text = " ".join(str(product.get(field) or "") for field in fields)
    text += " " + " ".join(str(details.get(field) or "") for field in
                         ("contract_code", "group_description", "contract_display_name"))
    return bool(re.search(r"BTC|BITCOIN", text.upper()))


class CoinbaseFuturesFeed:
    """Strict read-only connector: a spot BTC-USD cannot pass instrument validation."""

    def __init__(self, get: Callable[[str, dict], dict] = _public_get):
        self._get = get

    def list_bitcoin_futures(self) -> list[dict]:
        results: list[dict] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(10):
            params = {"product_type": "FUTURE", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            payload = self._get("/products", params)
            if not isinstance(payload.get("products"), list):
                raise ValueError("Coinbase product response is missing products")
            for product in payload["products"]:
                if _is_bitcoin_future(product) and product["product_id"] not in seen:
                    results.append(product)
                    seen.add(product["product_id"])
            pagination = payload.get("pagination") or {}
            if not pagination.get("has_next"):
                return results
            next_cursor = pagination.get("next_cursor")
            if not next_cursor or next_cursor == cursor:
                raise ValueError("Coinbase product pagination is inconsistent")
            cursor = next_cursor
        raise ValueError("Coinbase products exceeded 10 pages; refusing partial discovery")

    def require_bitcoin_future(self, product_id: str) -> dict:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{2,100}", product_id):
            raise ValueError("Invalid Coinbase product ID")
        payload = self._get(f"/products/{product_id}", {})
        product = payload.get("product", payload)
        if not isinstance(product, dict) or product.get("product_id") != product_id:
            raise ValueError("Coinbase returned unexpected product ID")
        if not _is_bitcoin_future(product):
            raise ValueError("Product is not a verified Bitcoin FUTURE; spot is forbidden")
        return product

    def closed_candles(self, product_id: str, timeframe: str = "M15",
                       count: int = 300, *, now: datetime | None = None) -> pd.DataFrame:
        if timeframe not in GRANULARITIES:
            raise ValueError(f"Unsupported Coinbase timeframe: {timeframe}")
        if not 52 <= count <= 350:
            raise ValueError("Candle count must be between 52 and 350")
        self.require_bitcoin_future(product_id)
        granularity, seconds = GRANULARITIES[timeframe]
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Current time must be timezone-aware")
        end = int(now.timestamp()) // seconds * seconds
        start = end - count * seconds
        payload = self._get(f"/products/{product_id}/candles", {
            "start": str(start), "end": str(end), "granularity": granularity,
            "limit": count,
        })
        rows = payload.get("candles")
        if not isinstance(rows, list) or not rows:
            raise ValueError("No futures candles available; do not substitute spot data")
        frame = pd.DataFrame(rows)
        required = {"start", "open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns):
            raise ValueError("Coinbase candle response is missing required fields")
        frame.index = pd.to_datetime(pd.to_numeric(frame.pop("start")), unit="s", utc=True)
        frame = frame.loc[frame.index.view("int64") // 1_000_000_000 + seconds <= end]
        if frame.empty:
            raise ValueError("Coinbase returned no completed candles")
        return validate_bars(frame.loc[:, ["open", "high", "low", "close", "volume"]])
