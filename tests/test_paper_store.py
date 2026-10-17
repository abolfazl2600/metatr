import json
import sqlite3
from dataclasses import asdict, replace
from decimal import Decimal

import pytest
from test_backtest import fixture

from metatr.contract_economics import SimulatedFill
from metatr.paper.store import PaperStore


def setup(tmp_path, account="test"):
    dataset, strategy, spec, costs, risk = fixture(tmp_path)
    store = PaperStore(tmp_path / "paper.sqlite")
    store.initialize(account, dataset.market, "1000", at_utc=dataset.start_utc)
    pins = {
        "execution": "PAPER_ONLY",
        "market": dataset.market.key,
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.version,
        "strategy_config_sha256": strategy.config_sha256,
        "data_sha256": dataset.data_sha256,
        "contract_spec": asdict(spec),
        "contract_metadata": dataset.provenance[0]["contract_metadata"],
        "cost_scenario": asdict(costs),
        "cost_sha256": costs.fingerprint,
    }
    run = store.register_run(account, pins, at_utc=dataset.start_utc)
    return store, run, pins, dataset, spec, costs


def fill(
    spec, costs, side="BUY", quantity="2", price="10000", fee="1", time="2020-01-01T00:15:00Z"
):
    return SimulatedFill(
        spec.market.key,
        time,
        side,
        quantity,
        price,
        str(Decimal(quantity) * Decimal(price) * Decimal(spec.multiplier)),
        fee,
        "USD",
        spec.fingerprint,
        costs.fingerprint,
    )


def intent(store, run, data, side="BUY", quantity="2", at="2020-01-01T00:15:00Z"):
    key = store.new_intent("test", run, at, side, quantity, data_sha256=data.data_sha256)
    store.transition(
        "test",
        key,
        "ACCEPTED",
        at_utc=at,
        reason="fixture approval",
        approval={"action": "APPROVE"},
    )
    return key


def test_exact_ledger_duplicate_restart_and_reversal(tmp_path):
    store, run, _, data, spec, costs = setup(tmp_path)
    key = intent(store, run, data)
    first = fill(spec, costs)
    store.fill("test", key, first, finalized_at_utc="2020-01-01T00:30:00Z")
    store.fill("test", key, first, finalized_at_utc="2020-01-01T00:30:00Z")
    assert len(store.inspect("test")["fills"]) == 1
    assert Decimal(store.inspect("test")["ledger"]["cash"]) == 999
    second = intent(store, run, data, "SELL", "4", "2020-01-01T00:30:00Z")
    store.fill(
        "test",
        second,
        fill(spec, costs, "SELL", "4", "10100", "2", "2020-01-01T00:30:00Z"),
        finalized_at_utc="2020-01-01T00:45:00Z",
    )
    store.mark("test", run, "10000", at_utc="2020-01-01T00:45:00Z")
    assert store.inspect("test")["ledger"]["contracts"] == "-2"
    assert Decimal(store.inspect("test")["marks"][-1]["data"]["equity"]) == 1001
    assert store.reconcile("test")
    assert PaperStore(store.path).reconcile("test")
    with pytest.raises(ValueError):
        store.transition("test", key, "CANCELLED", at_utc=data.end_utc, reason="illegal")
    with pytest.raises(ValueError):
        store.new_intent(
            "test", run, "2020-01-01T00:15:00Z", "SELL", "2", data_sha256=data.data_sha256
        )


def test_transaction_crash_rolls_back_every_effect(tmp_path, monkeypatch):
    store, run, _, data, spec, costs = setup(tmp_path)
    key = intent(store, run, data)
    original = store._reduce

    def crash(*args):
        original(*args)
        if args[3] == "FILL":
            raise OSError("injected disk/crash failure")

    monkeypatch.setattr(store, "_reduce", crash)
    with pytest.raises(OSError):
        store.fill("test", key, fill(spec, costs), finalized_at_utc="2020-01-01T00:30:00Z")
    assert store.inspect("test")["orders"][0]["status"] == "ACCEPTED"
    assert not store.inspect("test")["fills"]
    assert store.inspect("test")["ledger"]["cash"] == "1000"
    assert PaperStore(store.path).reconcile("test")


def test_halt_isolation_tamper_and_schema(tmp_path):
    store, run, _, data, spec, costs = setup(tmp_path)
    store.initialize("second", replace(data.market, timeframe="M5"), "2000", at_utc=data.start_utc)
    assert store.inspect("second")["ledger"]["cash"] == "2000"
    store.halt("test", "manual kill", at_utc=data.start_utc)
    assert PaperStore(store.path).inspect("test")["halted"]
    key = store.new_intent(
        "test", run, "2020-01-01T00:15:00Z", "BUY", "2", data_sha256=data.data_sha256
    )
    with pytest.raises(ValueError):
        store.transition(
            "test",
            key,
            "ACCEPTED",
            at_utc="2020-01-01T00:15:00Z",
            reason="blocked",
            approval={"action": "APPROVE"},
        )
    with pytest.raises(ValueError):
        store.reset_halt("test", "reason", at_utc=data.end_utc)
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE audit_events SET kind='bad'")
        ledger = store.inspect("test")["ledger"]
        ledger["cash"] = "999999"
        db.execute(
            "UPDATE paper_accounts SET ledger_json=? WHERE account='test'", (json.dumps(ledger),)
        )
    with pytest.raises(ValueError, match="projection mismatch"):
        store.reconcile("test")
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError):
        PaperStore(store.path)
