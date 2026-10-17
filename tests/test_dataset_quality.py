import json
import sqlite3
from dataclasses import replace

import pandas as pd
import pytest

from metatr.calendars import SessionCalendar
from metatr.data import sample_bars
from metatr.dataset_cli import main
from metatr.dataset_quality import validate_dataset
from metatr.futures_core import Market
from metatr.futures_store import FuturesSQLiteStore
from metatr.research import canonical, digest

MARKET = Market("coinbase", "BTC", "FUTURE", "BIP-20DEC30-CDE", "M15")


def save(store, bars, *, source="synthetic_fixture", asof=None):
    candles = [{"start": str(int(t.timestamp())), **{c: str(row[c]) for c in bars.columns}}
               for t, row in bars.iterrows()]
    return store.save_snapshot(MARKET, source=source, raw_payload={"candles": candles},
                               contract_metadata={"product_id": MARKET.product_id,
                                                  "product_type": "FUTURE",
                                                  "future_product_details": {
                                                      "contract_code": "BIP",
                                                      "contract_size": "0.01"}},
                               bars=bars, asof=asof or bars.index[-1] + pd.Timedelta(minutes=15))


def setup(tmp_path, gap=False):
    store = FuturesSQLiteStore(tmp_path / "bars.sqlite")
    bars = sample_bars(10)
    end = bars.index[-1] + pd.Timedelta(minutes=15)
    if gap:
        save(store, bars.iloc[:4])
        save(store, bars.iloc[5:])
    else:
        save(store, bars)
    return store, bars, end


def test_complete_bounded_and_stable_report(tmp_path):
    store, bars, end = setup(tmp_path)
    kwargs = dict(start_utc=bars.index[0], end_utc=end, asof_utc=end)
    report, dataset = validate_dataset(store, MARKET, **kwargs)
    assert report.ready and dataset is not None
    assert report.to_dict() == validate_dataset(store, MARKET, **kwargs)[0].to_dict()
    got = store.read_bars(MARKET, limit=1, **kwargs)
    assert len(got) == 10  # limit must not truncate explicit bounds
    with pytest.raises(ValueError):
        store.read_bars(MARKET, start_utc=bars.index[0])
    assert len(store.read_bars(MARKET, limit=2)) == 2
    args = ["--store", str(store.path), "--market", MARKET.key,
            "--start-utc", bars.index[0].isoformat(), "--end-utc", end.isoformat(),
            "--asof-utc", end.isoformat()]
    assert main(args) == 0


def test_separately_valid_batches_fail_and_backfill_plan(tmp_path):
    store, bars, end = setup(tmp_path, gap=True)
    report, dataset = validate_dataset(store, MARKET, bars.index[0], end, end)
    assert not report.ready and dataset is None
    assert "UNVERIFIED_GAP" in report.issues
    assert report.missing_windows == ((bars.index[4].isoformat(), bars.index[5].isoformat()),)
    save(store, bars.iloc[4:5])
    assert validate_dataset(store, MARKET, bars.index[0], end, end)[0].ready


def test_sourced_market_specific_calendar(tmp_path):
    store, bars, end = setup(tmp_path, gap=True)
    calendar = SessionCalendar(MARKET, "synthetic_session", "fixture-v1",
                               "synthetic://documented-test-only", "America/Chicago",
                               bars.index[0].isoformat(), end.isoformat(),
                               ((bars.index[4].isoformat(), bars.index[5].isoformat()),))
    report, dataset = validate_dataset(store, MARKET, bars.index[0], end, end, calendar=calendar)
    assert report.ready and len(dataset.bars) == 9
    assert report.calendar_version == "fixture-v1"
    wrong = replace(calendar, market=replace(MARKET, product_id="BIT-20DEC30-CDE"))
    with pytest.raises(ValueError):
        validate_dataset(store, MARKET, bars.index[0], end, end, calendar=wrong)
    invalid = replace(calendar, source="")
    with pytest.raises(ValueError):
        validate_dataset(store, MARKET, bars.index[0], end, end, calendar=invalid)


def test_asof_stale_and_closed_boundaries(tmp_path):
    store, bars, end = setup(tmp_path)
    older = end - pd.Timedelta(minutes=15)
    report, _ = validate_dataset(store, MARKET, bars.index[0], older, older)
    assert not report.ready  # snapshot had not arrived yet
    future = end + pd.Timedelta(hours=2)
    assert validate_dataset(store, MARKET, bars.index[0], end, future)[0].ready
    report, _ = validate_dataset(store, MARKET, bars.index[0], end, future, require_fresh=True)
    assert "STALE_LAST_CLOSED_BAR" in report.issues
    with pytest.raises(ValueError):
        validate_dataset(store, MARKET, bars.index[0], end, older)


@pytest.mark.parametrize("kind", ["disagreement", "duplicate", "wrong_product", "missing_raw"])
def test_raw_source_validation(tmp_path, kind):
    store, bars, end = setup(tmp_path)
    with sqlite3.connect(store.path) as db:
        row = db.execute("SELECT * FROM raw_snapshots").fetchone()
        sid, key, source, received, checksum, raw, metadata, schema = row
        payload, meta = json.loads(raw), json.loads(metadata)
        if kind == "disagreement":
            payload["candles"][0]["close"] = "99"
        elif kind == "duplicate":
            payload["candles"].append(payload["candles"][0])
        elif kind == "wrong_product":
            meta["product_id"] = "BCH-20DEC30-CDE"
        else:
            payload = {"fixture": True}
        raw, metadata = canonical(payload), canonical(meta)
        checksum = __import__('hashlib').sha256(raw.encode()).hexdigest()
        sid = digest({"market": key, "source": source, "payload_sha256": checksum,
                      "metadata": metadata})
        # Deliberately emulate corrupt/contradictory stored captures without using save_snapshot.
        db.execute("DELETE FROM raw_snapshots")
        db.execute("INSERT INTO raw_snapshots VALUES(?,?,?,?,?,?,?,?)",
                   (sid, key, source, received, checksum, raw, metadata, schema))
        db.execute("UPDATE verified_bars SET first_snapshot_id=?", (sid,))
    report, _ = validate_dataset(store, MARKET, bars.index[0], end, end)
    assert not report.ready
    assert {"disagreement": "SOURCE_DISAGREEMENT", "duplicate": "DUPLICATE_SOURCE_TIMESTAMP",
            "wrong_product": "INVALID_SOURCE_PROVENANCE",
            "missing_raw": "MISSING_RAW_CANDLE_PROVENANCE"}[kind] in report.issues
