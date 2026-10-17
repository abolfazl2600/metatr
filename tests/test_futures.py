import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from metatr.exchanges.coinbase_futures import CoinbaseFuturesFeed
from metatr.futures_core import Market, load_configuration, profile_for, select_model

FUTURE = {"product_id": "BTC-27NOV26-CDE", "product_type": "FUTURE",
          "display_name": "Bitcoin November Future", "future_product_details": {"contract_code": "BTC"}}
SPOT = {"product_id": "BTC-USD", "product_type": "SPOT", "display_name": "Bitcoin",
        "future_product_details": {}}
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def fake_get(path, params):
    if path == "/products":
        return {"products": [SPOT, FUTURE], "pagination": {"has_next": False}}
    if path.startswith("/products/") and not path.endswith("/candles"):
        return {"product": FUTURE if path.endswith(FUTURE["product_id"]) else SPOT}
    if path.endswith("/candles"):
        assert params["granularity"] == "FIFTEEN_MINUTE"
        starts = range(int(params["start"]), int(params["end"]) + 900, 900)
        return {"candles": [{"start": str(start), "open": str(30000 + i),
                             "high": str(30002 + i), "low": str(29999 + i),
                             "close": str(30001 + i), "volume": "12"}
                            for i, start in enumerate(starts)]}
    raise AssertionError(path)


def test_discover_only_real_bitcoin_futures():
    products = CoinbaseFuturesFeed(fake_get).list_bitcoin_futures()
    assert [x["product_id"] for x in products] == [FUTURE["product_id"]]


def test_spot_cannot_be_substituted():
    with pytest.raises(ValueError, match="not a verified Bitcoin FUTURE"):
        CoinbaseFuturesFeed(fake_get).closed_candles("BTC-USD", now=NOW)


def test_complete_candles_discard_live():
    bars = CoinbaseFuturesFeed(fake_get).closed_candles(FUTURE["product_id"], now=NOW)
    assert len(bars) == 300
    assert bars.index[-1] == pd.Timestamp("2026-10-17 11:45:00+00:00")
    assert bars["close"].iloc[-1] == 30300


def test_malformed_api_response_fails_closed():
    def broken(path, params):
        response = fake_get(path, params)
        if path.endswith("/candles"):
            return {"candles": [{"start": "1", "close": "12"}]}
        return response
    with pytest.raises(ValueError, match="missing required"):
        CoinbaseFuturesFeed(broken).closed_candles(FUTURE["product_id"], now=NOW)


def test_market_profile_is_per_exchange_and_contract():
    market = Market("coinbase", "BTC", "FUTURE", "BTC-27NOV26-CDE", "M15")
    other = Market("otherexchange", "BTC", "FUTURE", "BTC-USDT-PERP", "M15")
    cfg = {"profiles": {"coinbase:BTC:FUTURE:*:M15": {"models": ["generic"]},
                        market.key: {"models": ["exact"]}}}
    assert profile_for(market, cfg)["models"] == ["exact"]
    with pytest.raises(ValueError, match="No model profile"):
        profile_for(other, cfg)


def test_model_selector_only_uses_completed_bars_and_no_execution():
    bars = CoinbaseFuturesFeed(fake_get).closed_candles(FUTURE["product_id"], now=NOW)
    market = Market("coinbase", "BTC", "FUTURE", FUTURE["product_id"], "M15")
    cfg = load_configuration("config/markets.json")
    output = select_model(bars, market, cfg)
    assert output["execution"] == "DISABLED"
    assert output["source"] == "coinbase_public_futures"
    assert output["timestamp"] == bars.index[-1].isoformat()
    assert output["action"] in {"LONG", "SHORT", "NO_TRADE"}
    assert len(output["model_scores"]) == 2
    assert json.loads(json.dumps(output)) == output


def test_no_positive_model_means_no_trade():
    bars = CoinbaseFuturesFeed(fake_get).closed_candles(FUTURE["product_id"], now=NOW)
    market = Market("coinbase", "BTC", "FUTURE", FUTURE["product_id"], "M15")
    cfg = {"profiles": {market.key: {"models": [{"name": "ema_cross",
        "parameters": {"fast": 8, "slow": 21}}], "evaluation_bars": 80,
        "min_trades": 1000}}}
    output = select_model(bars, market, cfg)
    assert output["action"] == "NO_TRADE" and output["selected_model"] is None
