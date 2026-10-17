"""Coinbase Advanced Trade public US futures data; never routes orders."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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


# Coinbase Derivatives Exchange Bitcoin contracts. Do not infer identity from
# display names: "Bitcoin Cash" also contains the word "Bitcoin".
# Expand this registry only after verifying further contract roots with Coinbase.
BTC_CDE_ROOTS = {"BIP": "PERPETUAL_STYLE", "BIT": "DATED"}


def bitcoin_future_kind(product: dict) -> str | None:
    """Identify verified Coinbase BTC futures by native contract family."""
    if not isinstance(product, dict) or product.get("product_type") != "FUTURE":
        return None
    details = product.get("future_product_details")
    if not isinstance(details, dict):
        return None
    product_id = product.get("product_id")
    if not isinstance(product_id, str):
        return None
    match = re.fullmatch(r"(BIP|BIT)-[0-9]{2}[A-Z]{3}[0-9]{2}-CDE", product_id)
    if not match:
        return None
    root = match.group(1)
    contract_code = details.get("contract_code")
    if contract_code is not None and (
        not isinstance(contract_code, str) or contract_code.upper() != root
    ):
        return None
    # Treat explicit contradictory base/quote identifiers as unsafe. Some
    # derivatives responses omit the underlying currency fields altogether.
    for field, expected in (
        ("base_currency_id", "BTC"),
        ("base_display_symbol", "BTC"),
        ("quote_currency_id", "USD"),
        ("quote_display_symbol", "USD"),
    ):
        value = product.get(field)
        if value not in (None, "") and (
            not isinstance(value, str) or value.upper() != expected
        ):
            return None
    root_unit = details.get("contract_root_unit")
    if root_unit not in (None, "") and (
        not isinstance(root_unit, str) or root_unit.upper() != "BTC"
    ):
        return None
    return BTC_CDE_ROOTS[root]


def _is_bitcoin_future(product: dict) -> bool:
    return bitcoin_future_kind(product) is not None


def validate_contract_metadata(product: dict) -> str:
    """Validate structural metadata without converting perp pseudo-expiry to maturity."""
    from decimal import Decimal, InvalidOperation

    kind = bitcoin_future_kind(product)
    if kind is None:
        raise ValueError("Product is not a verified Bitcoin FUTURE; spot is forbidden")
    details = product["future_product_details"]
    size = details.get("contract_size")
    if size is not None:
        try:
            parsed_size = Decimal(str(size))
        except InvalidOperation as exc:
            raise ValueError("Invalid futures contract size") from exc
        if not parsed_size.is_finite() or parsed_size <= 0:
            raise ValueError("Invalid futures contract size")
    if kind == "DATED":
        expiry = details.get("contract_expiry")
        if not isinstance(expiry, str) or not expiry:
            raise ValueError("Dated futures must supply contract_expiry")
        try:
            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid dated futures contract_expiry") from exc
        if parsed.tzinfo is None:
            raise ValueError("Dated futures contract_expiry must have a timezone")
    else:
        # BIP perpetual-style contracts can have an API pseudo-expiry. Never
        # treat it as a closeout date or infer expiry from the product ID.
        perpetual = details.get("perpetual_details")
        if perpetual is not None and not isinstance(perpetual, dict):
            raise ValueError("Invalid perpetual_details")
        rate = details.get("funding_rate")
        if rate is not None and rate != "":
            try:
                parsed_rate = Decimal(str(rate))
            except InvalidOperation as exc:
                raise ValueError("Invalid perpetual funding_rate") from exc
            if not parsed_rate.is_finite():
                raise ValueError("Invalid perpetual funding_rate")
    return kind

@dataclass(frozen=True)
class CoinbaseCandleCapture:
    """Verified market capture with its exact public raw API response."""

    bars: pd.DataFrame
    raw_payload: dict
    product: dict
    captured_at: datetime


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
                    validate_contract_metadata(product)
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
        validate_contract_metadata(product)
        return product

    def closed_candles(self, product_id: str, timeframe: str = "M15",
                       count: int = 300, *, now: datetime | None = None) -> pd.DataFrame:
        """Backward-compatible convenience accessor without raw provenance."""
        return self.capture_candles(product_id, timeframe, count, now=now).bars

    def capture_candles(self, product_id: str, timeframe: str = "M15",
                        count: int = 300, *, now: datetime | None = None
                        ) -> CoinbaseCandleCapture:
        """Fetch product + public candles together with raw JSON provenance."""
        if timeframe not in GRANULARITIES:
            raise ValueError(f"Unsupported Coinbase timeframe: {timeframe}")
        if not 52 <= count <= 350:
            raise ValueError("Candle count must be between 52 and 350")
        product = self.require_bitcoin_future(product_id)
        granularity, seconds = GRANULARITIES[timeframe]
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Current time must be timezone-aware")
        end = int(now.timestamp()) // seconds * seconds
        start = end - count * seconds
        # Coinbase may include the candle whose start equals the requested end.
        # End one second before the next open to request exactly closed bars.
        payload = self._get(f"/products/{product_id}/candles", {
            "start": str(start), "end": str(end - 1), "granularity": granularity,
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
        frame = frame.loc[(frame.index.view("int64") // 1_000_000_000 >= start)
                          & (frame.index.view("int64") // 1_000_000_000 + seconds <= end)]
        if frame.empty:
            raise ValueError("Coinbase returned no completed candles")
        bars = validate_bars(frame.loc[:, ["open", "high", "low", "close", "volume"]])
        timestamps = bars.index.view("int64") // 1_000_000_000
        if (timestamps % seconds != 0).any():
            raise ValueError("Futures candles are not aligned to their timeframe")
        if len(bars) != count or (len(timestamps) > 1 and
                                  (timestamps[1:] - timestamps[:-1] != seconds).any()):
            raise ValueError("Missing futures candles or timeline gaps; refuse research signal")
        return CoinbaseCandleCapture(bars=bars, raw_payload=payload, product=product,
                                     captured_at=now.astimezone(timezone.utc))
