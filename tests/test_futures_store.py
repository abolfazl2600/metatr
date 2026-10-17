"""Offline tests for the immutable SQLite public-futures research store."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from metatr.data import sample_bars
from metatr.futures_core import Market
from metatr.futures_store import SCHEMA_VERSION, FuturesSQLiteStore

MARKET = Market("coinbase", "BTC", "FUTURE", "BIP-20DEC30-CDE", "M15")
OTHER = Market("coinbase", "BTC", "FUTURE", "BIT-27NOV26-CDE", "M15")
ASOF = datetime(2020, 1, 1, 1, 15, tzinfo=timezone.utc)


def snapshot_args():
    return {
        "source": "test_fixture",
        "raw_payload": {"candles": [{"start": "1577836800", "open": "100"}]},
        "contract_metadata": {
            "product_id": MARKET.product_id,
            "future_product_details": {"contract_code": "BIP"},
        },
        "bars": sample_bars(5),
        "asof": ASOF,
    }


def test_persist_reload_and_exact_raw_provenance(tmp_path):
    path = tmp_path / "futures.sqlite"
    store = FuturesSQLiteStore(path)
    expected = snapshot_args()
    sid = store.save_snapshot(MARKET, **expected)

    recovered = FuturesSQLiteStore(path).read_snapshot(sid)
    assert recovered["market"] == MARKET.key
    assert recovered["schema_version"] == SCHEMA_VERSION
    assert recovered["raw_payload"] == expected["raw_payload"]
    assert recovered["contract_metadata"] == expected["contract_metadata"]
    assert recovered["source"] == "test_fixture"
    bars = FuturesSQLiteStore(path).read_bars(MARKET)
    assert len(bars) == 5
    assert bars.index.tz is not None
    pd.testing.assert_frame_equal(bars, expected["bars"], check_dtype=False)


def test_repeat_ingestion_is_idempotent_and_market_isolation(tmp_path):
    store = FuturesSQLiteStore(tmp_path / "market.db")
    sid = store.save_snapshot(MARKET, **snapshot_args())
    assert store.save_snapshot(MARKET, **snapshot_args()) == sid
    assert len(store.read_bars(MARKET, limit=3)) == 3
    assert store.read_bars(MARKET, limit=3).index[0] == pd.Timestamp(
        "2020-01-01T00:30:00+00:00"
    )
    with pytest.raises(ValueError, match="No stored candles"):
        store.read_bars(OTHER)


def test_conflicting_candle_never_overwrites_history(tmp_path):
    store = FuturesSQLiteStore(tmp_path / "immutable.db")
    sid = store.save_snapshot(MARKET, **snapshot_args())
    original = store.read_bars(MARKET)

    update = snapshot_args()
    changed = update["bars"].copy()
    changed.iloc[-1, changed.columns.get_loc("close")] += 0.01
    changed.iloc[-1, changed.columns.get_loc("high")] += 1.0
    update["bars"] = changed
    update["raw_payload"] = {"candles": [{"start": "change"}]}

    with pytest.raises(ValueError, match="Conflicting verified candle"):
        store.save_snapshot(MARKET, **update)
    assert store.read_snapshot(sid)["raw_payload"] == snapshot_args()["raw_payload"]
    pd.testing.assert_frame_equal(store.read_bars(MARKET), original)


def test_open_and_misaligned_bars_fail_closed(tmp_path):
    store = FuturesSQLiteStore(tmp_path / "closed.db")
    data = snapshot_args()
    data["asof"] = ASOF - timedelta(seconds=1)
    with pytest.raises(ValueError, match="still-forming"):
        store.save_snapshot(MARKET, **data)

    data = snapshot_args()
    data["bars"] = data["bars"].copy()
    data["bars"].index = data["bars"].index + pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="aligned"):
        store.save_snapshot(MARKET, **data)


def test_wrong_contract_and_naive_time_rejected(tmp_path):
    store = FuturesSQLiteStore(tmp_path / "guard.db")
    data = snapshot_args()
    data["contract_metadata"] = {"product_id": "BCH-27NOV26-CDE"}
    with pytest.raises(ValueError, match="does not match market identity"):
        store.save_snapshot(MARKET, **data)

    data = snapshot_args()
    data["asof"] = datetime(2020, 1, 1, 1, 15)
    with pytest.raises(ValueError, match="timezone-aware"):
        store.save_snapshot(MARKET, **data)


def test_unknown_schema_refused(tmp_path):
    import sqlite3

    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="Unsupported SQLite schema version"):
        FuturesSQLiteStore(path)
