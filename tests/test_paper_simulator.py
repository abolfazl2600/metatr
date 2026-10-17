from dataclasses import replace
from decimal import Decimal

from test_paper_risk import inputs

from metatr.backtest import BacktestEngine
from metatr.paper.risk import PaperMarketState
from metatr.paper.simulator import PaperExecutionSimulator
from metatr.paper.store import PaperStore
from metatr.validation import slice_dataset


def test_next_bar_fill_idempotence_reopen_and_backtest_parity(tmp_path):
    store, key, _, _, first_health, policy, spec, now = inputs(tmp_path)
    simulator = PaperExecutionSimulator(store)
    assert simulator.approve("test", key, first_health, policy, spec, now) == "ACCEPTED"
    assert simulator.process_next_bar("test", key, first_health, policy, spec, now) is None
    from test_backtest import fixture

    args = fixture(tmp_path)
    health = PaperMarketState(slice_dataset(args[0], 0, 2), first_health.costs)
    fill = simulator.process_next_bar("test", key, health, policy, spec, health.dataset.end_utc)
    assert fill["fill"]["timestamp"] == "2020-01-01T00:15:00+00:00"
    assert fill["fill"]["price"] == "10011"
    assert Decimal(store.inspect("test")["ledger"]["cash"]) == Decimal("999.79978")
    baseline = BacktestEngine().run(*args, baseline="buy_hold").to_dict()
    assert baseline["fills"][0]["price"] == fill["fill"]["price"]
    assert baseline["fills"][0]["fee"] == fill["fill"]["fee"]
    restarted = PaperExecutionSimulator(PaperStore(store.path))
    assert (
        restarted.process_next_bar("test", key, health, policy, spec, health.dataset.end_utc)
        == fill
    )
    assert len(store.inspect("test")["fills"]) == 1
    assert store.reconcile("test")


def test_missing_next_bar_expires_and_halts(tmp_path):
    store, key, _, _, first_health, policy, spec, now = inputs(tmp_path)
    sim = PaperExecutionSimulator(store)
    sim.approve("test", key, first_health, policy, spec, now)
    from test_backtest import fixture

    args = fixture(tmp_path)
    skipped = PaperMarketState(slice_dataset(args[0], 0, 3), first_health.costs)
    assert sim.process_next_bar("test", key, skipped, policy, spec, skipped.dataset.end_utc) is None
    assert store.inspect("test")["orders"][0]["status"] == "EXPIRED"
    assert store.inspect("test")["halted"]
    assert not store.inspect("test")["fills"]


def test_policy_change_and_source_failure_never_fill(tmp_path):
    store, key, _, _, health, policy, spec, now = inputs(tmp_path)
    sim = PaperExecutionSimulator(store)
    sim.approve("test", key, health, policy, spec, now)
    changed = replace(policy, version="2.0.0")
    assert sim.process_next_bar("test", key, health, changed, spec, now) is None
    assert store.inspect("test")["orders"][0]["status"] == "EXPIRED"
    assert not store.inspect("test")["fills"]


def test_source_outage_latches_halt_and_expires_pending(tmp_path):
    from test_backtest import fixture

    store, key, _, _, first, policy, spec, now = inputs(tmp_path)
    sim = PaperExecutionSimulator(store)
    sim.approve("test", key, first, policy, spec, now)
    args = fixture(tmp_path)
    health = PaperMarketState(slice_dataset(args[0], 0, 2), first.costs, source_health="HTTP_403")
    assert sim.process_next_bar("test", key, health, policy, spec, health.dataset.end_utc) is None
    assert store.inspect("test")["halted"]
    assert not store.inspect("test")["fills"]
    assert store.reconcile("test")
