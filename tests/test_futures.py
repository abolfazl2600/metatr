import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from metatr.exchanges.coinbase_futures import (
    CoinbaseFuturesFeed,
    bitcoin_future_kind,
    validate_contract_metadata,
)
from metatr.futures_core import Market, load_configuration, profile_for, select_model

FUTURE = {"product_id": "BIT-27NOV26-CDE", "product_type": "FUTURE",
          "display_name": "Bitcoin November Future",
          "future_product_details": {"contract_code": "BIT",
                                     "contract_expiry": "2026-11-27T16:00:00Z",
                                     "contract_size": "0.01"}}
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
    market = Market("coinbase", "BTC", "FUTURE", "BIT-27NOV26-CDE", "M15")
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


PERP = {
    "product_id": "BIP-20DEC30-CDE",
    "product_type": "FUTURE",
    "display_name": "BTC PERP",
    "future_product_details": {
        "contract_code": "BIP", "contract_expiry": "2089-12-20T00:00:00Z",
        "contract_size": "0.01", "funding_rate": "-0.0001",
        "perpetual_details": {"funding_rate": "-0.0001"},
    },
}
BCH_PERP = {
    "product_id": "BCP-20DEC30-CDE", "product_type": "FUTURE",
    "display_name": "Bitcoin Cash Perpetual",
    "future_product_details": {"contract_code": "BCP", "group_description": "Bitcoin Cash"},
}
BCH_DATED = {
    "product_id": "BCH-27NOV26-CDE", "product_type": "FUTURE",
    "display_name": "Bitcoin Cash November Futures",
    "future_product_details": {"contract_code": "BCH", "group_description": "Bitcoin Cash"},
}


def test_bitcoin_cash_contracts_never_pass_discovery_or_detail_validation():
    def get(path, params):
        if path == "/products":
            return {"products": [BCH_PERP, BCH_DATED, PERP, FUTURE],
                    "pagination": {"has_next": False}}
        if path.startswith("/products/"):
            return {"product": BCH_PERP if "BCP" in path else BCH_DATED}
        raise AssertionError(path)

    feed = CoinbaseFuturesFeed(get)
    assert [p["product_id"] for p in feed.list_bitcoin_futures()] == [
        PERP["product_id"], FUTURE["product_id"],
    ]
    assert bitcoin_future_kind(BCH_PERP) is None
    assert bitcoin_future_kind(BCH_DATED) is None
    for rejected in (BCH_PERP["product_id"], BCH_DATED["product_id"]):
        with pytest.raises(ValueError, match="not a verified Bitcoin FUTURE"):
            feed.require_bitcoin_future(rejected)


def test_perpetual_and_dated_metadata_remain_distinct():
    assert validate_contract_metadata(PERP) == "PERPETUAL_STYLE"
    assert validate_contract_metadata(FUTURE) == "DATED"
    assert bitcoin_future_kind(PERP) == "PERPETUAL_STYLE"
    # A far-future pseudo-expiry is not interpreted as a liquidation/maturity date.


def test_conflicting_asset_metadata_fails_closed():
    for field, value in (
        ("base_currency_id", "BCH"),
        ("base_display_symbol", "BCH"),
        ("quote_currency_id", "EUR"),
    ):
        altered = {**PERP, field: value}
        assert bitcoin_future_kind(altered) is None
    wrong_code = {**PERP, "future_product_details": {
        **PERP["future_product_details"], "contract_code": "BCP"}}
    assert bitcoin_future_kind(wrong_code) is None
    invalid_size = {**PERP, "future_product_details": {
        **PERP["future_product_details"], "contract_size": "NaN"}}
    with pytest.raises(ValueError, match="contract size"):
        validate_contract_metadata(invalid_size)


def test_missing_maturity_rejected_for_dated_futures():
    without_expiry = {**FUTURE, "future_product_details": {"contract_code": "BIT"}}
    with pytest.raises(ValueError, match="contract_expiry"):
        validate_contract_metadata(without_expiry)


def test_gap_in_futures_candles_blocks_analysis():
    def missing_one(path, params):
        response = fake_get(path, params)
        if path.endswith("/candles"):
            response["candles"].pop(10)
        return response

    with pytest.raises(ValueError, match="timeline gaps"):
        CoinbaseFuturesFeed(missing_one).closed_candles(FUTURE["product_id"], now=NOW)


def test_out_of_order_candles_sorted_but_still_valid():
    def reverse(path, params):
        response = fake_get(path, params)
        if path.endswith("/candles"):
            response["candles"] = list(reversed(response["candles"]))
        return response

    bars = CoinbaseFuturesFeed(reverse).closed_candles(FUTURE["product_id"], now=NOW)
    assert len(bars) == 300
    assert bars.index.is_monotonic_increasing


def test_cli_live_probe_contract_offline(monkeypatch, capsys):
    from metatr import futures_cli

    monkeypatch.setattr(futures_cli, "CoinbaseFuturesFeed",
                        lambda: CoinbaseFuturesFeed(fake_get))
    assert futures_cli.main(["--product-id", FUTURE["product_id"],
                             "--probe-candles", "--count", "64"]) == 0
    probe = json.loads(capsys.readouterr().out)
    assert probe["mode"] == "PUBLIC_DATA_PROBE"
    assert probe["contract_kind"] == "DATED"
    assert probe["count"] == 64
    assert probe["validated_contiguous"] is True
    assert probe["execution"] == "DISABLED"
    assert probe["spot_substitution"] is False



def test_live_coinbase_perpetual_empty_optional_base_currency_fields():
    actual_metadata = {
        **PERP, "base_currency_id": "", "base_display_symbol": "",
        "quote_currency_id": "USD", "quote_display_symbol": "USD",
        "future_product_details": {
            **PERP["future_product_details"], "contract_root_unit": "BTC",
            "contract_expiry_type": "EXPIRING",
        },
    }
    assert validate_contract_metadata(actual_metadata) == "PERPETUAL_STYLE"
    not_bitcoin = {**actual_metadata, "future_product_details": {
        **actual_metadata["future_product_details"], "contract_root_unit": "BCH"}}
    assert bitcoin_future_kind(not_bitcoin) is None



def test_candle_request_excludes_open_boundary_from_coinbase_query():
    seen = []

    def capture_get(path, params):
        if path.endswith("/candles"):
            seen.append(params.copy())
        return fake_get(path, params)

    bars = CoinbaseFuturesFeed(capture_get).closed_candles(FUTURE["product_id"], now=NOW)
    assert len(bars) == 300
    assert seen[0]["end"] == str(int(NOW.timestamp()) - 1)
    assert seen[0]["limit"] == 300


def test_capture_preserves_raw_candles_and_verified_metadata():
    capture = CoinbaseFuturesFeed(fake_get).capture_candles(FUTURE["product_id"], now=NOW,
                                                           count=64)
    assert capture.product == FUTURE
    assert capture.raw_payload["candles"][0]["start"]
    assert len(capture.bars) == 64
    assert capture.captured_at == NOW


def test_cli_atomic_ingest_and_recovery(monkeypatch, capsys, tmp_path):
    from metatr import futures_cli
    from metatr.futures_core import Market
    from metatr.futures_store import FuturesSQLiteStore

    monkeypatch.setattr(futures_cli, "CoinbaseFuturesFeed",
                        lambda: CoinbaseFuturesFeed(fake_get))
    database = tmp_path / "futures.db"
    args = ["--product-id", FUTURE["product_id"], "--probe-candles",
            "--count", "64", "--store", str(database)]
    assert futures_cli.main(args) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["execution"] == "DISABLED"
    snapshot = response["storage"]["snapshot_id"]
    store = FuturesSQLiteStore(database)
    recovered = store.read_snapshot(snapshot)
    assert recovered["contract_metadata"]["product_id"] == FUTURE["product_id"]
    assert "candles" in recovered["raw_payload"]
    market = Market("coinbase", "BTC", "FUTURE", FUTURE["product_id"], "M15")
    assert len(store.read_bars(market)) == 64


def test_cli_rejects_spot_and_does_not_create_store(monkeypatch, tmp_path):
    from metatr import futures_cli

    monkeypatch.setattr(futures_cli, "CoinbaseFuturesFeed",
                        lambda: CoinbaseFuturesFeed(fake_get))
    database = tmp_path / "should_not_exist.db"
    args = ["--product-id", SPOT["product_id"], "--probe-candles",
            "--count", "64", "--store", str(database)]
    assert futures_cli.main(args) == 1
    assert not database.exists()
