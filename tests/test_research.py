import json
import socket
import sqlite3
from dataclasses import replace

import pandas as pd
import pytest

from metatr.data import sample_bars
from metatr.futures_core import Market
from metatr.futures_store import FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry, StrategySpec
from metatr.research import read_dataset, replay, write_experiment
from metatr.research_cli import main

MARKET = Market("coinbase", "BTC", "FUTURE", "BIP-20DEC30-CDE", "M15")


def seed(tmp_path, market=MARKET, count=40):
    store = FuturesSQLiteStore(tmp_path / (market.product_id + ".sqlite"))
    bars = sample_bars(count)
    end = bars.index[-1] + pd.Timedelta(minutes=15)
    product = {"product_id": market.product_id, "product_type": "FUTURE",
               "future_product_details": {"contract_code": market.product_id[:3],
                                          "contract_size": "0.01",
                                          "contract_expiry": "2030-12-20T00:00:00Z"}}
    store.save_snapshot(market, source="synthetic_fixture", raw_payload={"fixture": count},
                        contract_metadata=product, bars=bars, asof=end)
    registry = StrategyRegistry(tmp_path / "registry.sqlite")
    spec = registry.register(StrategySpec.create(
        market, "momentum", "1.0.0", "momentum", {"lookback": 8, "threshold": 0.001},
        created_at="2020-01-01T00:00:00Z", status="research"))
    return store, registry, spec, bars, end


def test_deterministic_offline_replay_and_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network"))
    store, registry, spec, bars, end = seed(tmp_path)
    dataset = read_dataset(store, MARKET, bars.index[0], end)
    a, b = replay(dataset, spec), replay(dataset, spec)
    assert a == b
    assert a["action"] == "NO_TRADE" and a["backtest_validated"] is False
    target = write_experiment(a, tmp_path / "runs")
    assert target == write_experiment(b, tmp_path / "runs")
    assert json.loads(target.read_text())["bar_count"] == 40
    args = ["--store", str(store.path), "--registry", str(registry.path),
            "--market", MARKET.key, "--strategy-id", "momentum",
            "--strategy-version", "1.0.0", "--start-utc", bars.index[0].isoformat(),
            "--end-utc", end.isoformat(), "--output", str(tmp_path / "cli"), "--offline-only"]
    assert main(args) == 0
    registry.retire(MARKET, "momentum", "1.0.0")
    assert main(args) == 1


def test_contract_isolation_and_immutable_dataset(tmp_path):
    store, _, spec, bars, end = seed(tmp_path)
    first = read_dataset(store, MARKET, bars.index[0], end)
    other = replace(MARKET, product_id="BIT-20DEC30-CDE")
    second_store, _, second_spec, _, _ = seed(tmp_path, other)
    second = read_dataset(second_store, other, bars.index[0], end)
    assert replay(first, spec)["run_id"] != replay(second, second_spec)["run_id"]
    changed = first.bars
    changed.iloc[0, 0] = 999
    assert first.bars.iloc[0, 0] != 999
    with pytest.raises(ValueError):
        replay(first, second_spec)


@pytest.mark.parametrize("kind", ["gap", "provenance", "mixed", "checksum", "metadata"])
def test_corrupt_dataset_fails(tmp_path, kind):
    store, _, _, bars, end = seed(tmp_path)
    with sqlite3.connect(store.path) as db:
        if kind == "gap":
            db.execute("DELETE FROM verified_bars WHERE candle_open_utc=?",
                       (bars.index[10].isoformat(),))
        elif kind == "provenance":
            db.execute("UPDATE verified_bars SET first_snapshot_id='missing'")
        elif kind == "mixed":
            db.execute("UPDATE raw_snapshots SET market_key='wrong'")
        elif kind == "checksum":
            db.execute("UPDATE raw_snapshots SET payload_json='{}'")
        else:
            db.execute("UPDATE raw_snapshots SET metadata_json='{}'")
    with pytest.raises(ValueError):
        read_dataset(store, MARKET, bars.index[0], end)


def test_range_asof_and_output_guards(tmp_path):
    store, _, spec, bars, end = seed(tmp_path)
    for start, finish, asof in [(end, bars.index[0], end),
                                (bars.index[0], end, bars.index[-1]),
                                (bars.index[0], end + pd.Timedelta(minutes=15), None)]:
        with pytest.raises(ValueError):
            read_dataset(store, MARKET, start, finish, asof_utc=asof)
    result = replay(read_dataset(store, MARKET, bars.index[0], end), spec)
    target = write_experiment(result, tmp_path / "out")
    target.write_text("conflict")
    with pytest.raises(ValueError):
        write_experiment(result, tmp_path / "out")
