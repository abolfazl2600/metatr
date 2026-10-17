"""Offline regression tests for the immutable exact-market strategy registry."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from metatr.futures_core import Market
from metatr.model_registry import (
    RegistrationNotFound,
    RegistryError,
    SchemaVersionError,
    StrategyRegistry,
    StrategySpec,
    VersionConflict,
    import_legacy_config,
)
from metatr.registry_cli import main as cli_main

BASE = Market("coinbase", "BTC", "FUTURE", "BIP-20DEC30-CDE", "M15")


def spec(market=BASE, *, version="1.0.0", params=None, status="draft", created_at=None):
    return StrategySpec.create(
        market, "ema_cross", version, "ema_cross", params or {"fast": 12, "slow": 26},
        evaluation={"evaluation_bars": 80, "min_trades": 3,
                    "round_trip_cost_bps": 12},
        status=status, created_at=created_at,
    )


def test_exact_market_and_timeframe_isolation(tmp_path):
    store = StrategyRegistry(tmp_path / "registry.sqlite")
    store.register(spec())
    assert store.get(BASE, "ema_cross", "1.0.0") is not None
    for market in (
        Market("coinbase", "BTC", "FUTURE", "BIT-20DEC30-CDE", "M15"),
        Market("coinbase", "BCH", "FUTURE", BASE.product_id, "M15"),
        Market("coinbase", "BTC", "SPOT", BASE.product_id, "M15"),
        Market("coinbase", "BTC", "FUTURE", BASE.product_id, "M5"),
        Market("other", "BTC", "FUTURE", BASE.product_id, "M15"),
        Market("coinbase", "BTC", "FUTURE", "BIP-20MAR31-CDE", "M15"),
    ):
        assert store.get(market, "ema_cross", "1.0.0") is None
        with pytest.raises(RegistrationNotFound):
            store.resolve(market, "ema_cross", "1.0.0")
    assert len(store.list()) == 1


def test_immutable_version_idempotence_and_reopen(tmp_path):
    path = tmp_path / "registry.sqlite"
    store = StrategyRegistry(path)
    original = spec(created_at="2026-10-17T10:00:00Z")
    assert store.register(original) == original
    # Duplicate registration is idempotent even with a different creation timestamp.
    assert store.register(spec(created_at="2026-10-19T10:00:00Z")) == original
    other = spec(version="1.0.1")
    store.register(other)
    assert len(StrategyRegistry(path).list()) == 2
    with pytest.raises(FrozenInstanceError):
        original.version = "2.0.0"
    local_params = original.parameters
    local_params["fast"] = 1
    assert original.parameters["fast"] == 12
    with pytest.raises(VersionConflict):
        store.register(spec(params={"fast": 10, "slow": 26}))
    assert store.resolve(BASE, "ema_cross", "1.0.0") == original


def test_canonical_sha_is_stable_and_sensitive_to_identity():
    a = spec(created_at="2026-10-17T10:00:00Z")
    b = spec(params={"slow": 26, "fast": 12}, created_at="2026-10-17T11:00:00Z")
    assert a.config_sha256 == b.config_sha256
    assert a.config_sha256 != spec(version="1.0.1").config_sha256
    assert a.config_sha256 != spec(market=Market("coinbase", "BTC", "FUTURE",
                                                       BASE.product_id, "H1")).config_sha256
    assert len(a.config_sha256) == 64
    assert a.to_dict()["execution"] == "DISABLED"


def test_conflict_rollback_and_sql_immutability(tmp_path):
    store = StrategyRegistry(tmp_path / "registry.sqlite")
    store.register(spec())
    with pytest.raises(VersionConflict):
        store.register(spec(params={"fast": 5, "slow": 26}))
    assert len(store.list()) == 1
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE strategy_versions SET parameters_json = '{}' ")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM strategy_versions")
    assert store.get(BASE, "ema_cross", "1.0.0").parameters["fast"] == 12


def test_retirement_is_irreversible_and_idempotent(tmp_path):
    store = StrategyRegistry(tmp_path / "registry.sqlite")
    original = store.register(spec())
    retired = store.retire(BASE, "ema_cross", "1.0.0")
    assert retired.status == "retired"
    assert retired.config_sha256 == original.config_sha256
    assert store.retire(BASE, "ema_cross", "1.0.0") == retired
    assert store.register(original) == retired
    assert store.list(status="retired") == [retired]
    with pytest.raises(RegistrationNotFound):
        store.retire(BASE, "ema_cross", "4.0.0")
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE strategy_versions SET status='draft'")


def test_reject_unknown_schema_and_nonregistry_db(tmp_path):
    path = tmp_path / "schema.sqlite"
    StrategyRegistry(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=7")
    with pytest.raises(SchemaVersionError):
        StrategyRegistry(path)
    path2 = tmp_path / "futures.sqlite"
    with sqlite3.connect(path2) as db:
        db.execute("CREATE TABLE snapshots (id INTEGER)")
        db.execute("PRAGMA user_version=1")
    with pytest.raises(SchemaVersionError):
        StrategyRegistry(path2)


@pytest.mark.parametrize("bad", [
    {"fast": 26, "slow": 12}, {"fast": True, "slow": 26},
    {"fast": 12.5, "slow": 26}, {"fast": 12, "slow": 26, "code": "os.system"},
    {"fast": 12}, {"fast": 12, "slow": float("nan")},
])
def test_invalid_parameters(bad):
    with pytest.raises(RegistryError):
        spec(params=bad)


def test_unknown_implementation_and_invalid_requirements():
    with pytest.raises(RegistryError):
        StrategySpec.create(BASE, "bad", "1.0.0", "os.system", {})
    with pytest.raises(RegistryError):
        StrategySpec.create(BASE, "ema_cross", "1.0.0", "ema_cross",
                            {"fast": 12, "slow": 26}, warmup_bars=10)
    with pytest.raises(RegistryError):
        StrategySpec.create(BASE, "ema_cross", "1.0.0", "ema_cross",
                            {"fast": 12, "slow": 26}, features=("volume",))
    with pytest.raises(RegistryError):
        StrategySpec.create(BASE, "ema_cross", "1.0", "ema_cross",
                            {"fast": 12, "slow": 26})
    with pytest.raises(RegistryError):
        spec(market=Market("coinbase", "BTC", "FUTURE", "*", "M15"))
    with pytest.raises(RegistryError):
        spec(market=Market("coinbase", "BTC", "FUTURE", "", "M15"))


def _legacy_file(path: Path) -> Path:
    path.write_text(json.dumps({"profiles": {
        "coinbase:BTC:FUTURE:*:M15": {
            "models": [
                {"name": "ema_cross", "parameters": {"fast": 12, "slow": 26}},
                {"name": "momentum", "parameters": {"lookback": 8, "threshold": 0.001}},
            ],
            "evaluation_bars": 80, "min_trades": 3, "round_trip_cost_bps": 12,
        }
    }}), encoding="utf-8")
    return path


def test_legacy_import_requires_explicit_wildcard(tmp_path):
    path = _legacy_file(tmp_path / "config.json")
    registry = StrategyRegistry(tmp_path / "registry.sqlite")
    with pytest.raises(RegistrationNotFound):
        import_legacy_config(registry, BASE, path)
    entries = import_legacy_config(registry, BASE, path, allow_wildcard=True)
    assert {entry.strategy_id for entry in entries} == {"ema_cross", "momentum"}
    assert all(entry.market == BASE for entry in entries)
    assert all(entry.evaluation == {
        "evaluation_bars": 80, "min_trades": 3, "round_trip_cost_bps": 12,
    } for entry in entries)
    assert {entry.parameters_json for entry in entries} == {
        '{"fast":12,"slow":26}', '{"lookback":8,"threshold":0.001}',
    }
    assert import_legacy_config(registry, BASE, path, allow_wildcard=True) == entries
    assert len(registry.list()) == 2
    # A wildcard M15 never authorizes M5 registration.
    with pytest.raises(RegistrationNotFound):
        import_legacy_config(registry, Market("coinbase", "BTC", "FUTURE",
                                              BASE.product_id, "M5"), path,
                             allow_wildcard=True)


def test_exact_profile_overrides_wildcard(tmp_path):
    path = _legacy_file(tmp_path / "config.json")
    data = json.loads(path.read_text())
    data["profiles"][BASE.key] = {"models": [
        {"name": "momentum", "parameters": {"lookback": 9, "threshold": 0.02}},
    ]}
    path.write_text(json.dumps(data), encoding="utf-8")
    registry = StrategyRegistry(tmp_path / "db.sqlite")
    result = import_legacy_config(registry, BASE, path, allow_wildcard=True)
    assert len(result) == 1
    assert result[0].parameters["lookback"] == 9


def test_legacy_conflict_does_not_overwrite(tmp_path):
    path = _legacy_file(tmp_path / "config.json")
    registry = StrategyRegistry(tmp_path / "db.sqlite")
    registry.register(spec(params={"fast": 10, "slow": 30}))
    with pytest.raises(VersionConflict):
        import_legacy_config(registry, BASE, path, allow_wildcard=True)
    assert len(registry.list()) == 1


def test_cli_list_show_retire_and_failure(tmp_path, capsys):
    path = _legacy_file(tmp_path / "config.json")
    db = str(tmp_path / "registry.sqlite")
    flags = ["--exchange", "coinbase", "--asset", "BTC", "--instrument", "FUTURE",
             "--product-id", BASE.product_id, "--timeframe", "M15"]
    assert cli_main(["--db", db, "import-legacy", *flags,
                     "--config", str(path), "--allow-wildcard"]) == 0
    capsys.readouterr()
    assert cli_main(["--db", db, "list", *flags]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert cli_main(["--db", db, "show", *flags, "--strategy-id", "ema_cross",
                     "--version", "1.0.0"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["strategy_id"] == "ema_cross"
    assert cli_main(["--db", db, "retire", *flags, "--strategy-id", "ema_cross",
                     "--version", "1.0.0"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "retired"
    assert cli_main(["--db", db, "show", *flags, "--strategy-id", "ema_cross",
                     "--version", "2.0.0"]) == 1
    assert "No exact registration" in capsys.readouterr().err


def test_atomic_import_when_late_model_conflicts(tmp_path):
    path = _legacy_file(tmp_path / "config.json")
    registry = StrategyRegistry(tmp_path / "db.sqlite")
    registry.register(StrategySpec.create(
        BASE, "momentum", "1.0.0", "momentum",
        {"lookback": 50, "threshold": 0.2},
    ))
    with pytest.raises(VersionConflict):
        import_legacy_config(registry, BASE, path, allow_wildcard=True)
    # No EMA should survive the rolled-back batch.
    assert len(registry.list()) == 1
    assert registry.get(BASE, "ema_cross", "1.0.0") is None
