from dataclasses import replace

import pandas as pd
import pytest
from test_paper_store import setup

from metatr.paper.risk import PaperMarketState, PaperRiskEngine, PaperRiskPolicy, evaluate
from metatr.paper.store import PaperStore
from metatr.validation import slice_dataset


def inputs(tmp_path):
    store, run, _, dataset, spec, costs = setup(tmp_path)
    prefix = slice_dataset(dataset, 0, 1)
    now = prefix.end_utc
    key = store.new_intent("test", run, now, "BUY", "2", data_sha256=prefix.data_sha256)
    state = store.inspect("test")
    item = state["intents"][0]["data"]
    policy = PaperRiskPolicy(
        "test",
        dataset.market,
        "fixture",
        "1.0.0",
        "1000",
        "USD",
        "1000",
        "1000",
        "10",
        "1000",
        "1000",
        "1",
        1,
        1,
        0,
        "100",
        "0.2",
        2,
        "ASSUMED_FULLY_COLLATERALIZED",
        "Hypothetical paper full collateral; no venue liquidation model",
    )
    health = PaperMarketState(prefix, costs)
    return store, key, item, state, health, policy, spec, now


@pytest.mark.parametrize(
    "field,boundary,below,reason",
    [
        ("max_position_notional", "200.22002", "200.22001", "POSITION_NOTIONAL_LIMIT"),
        ("max_order_notional", "200.02", "200.019", "ORDER_NOTIONAL_LIMIT"),
        ("max_gross_exposure", "200.22002", "200.22001", "GROSS_EXPOSURE_LIMIT"),
        ("max_net_exposure", "200.22002", "200.22001", "NET_EXPOSURE_LIMIT"),
        ("max_open_contracts", "2", "1.999", "CONTRACT_LIMIT"),
    ],
)
def test_precise_risk_boundaries(tmp_path, field, boundary, below, reason):
    _, _, item, state, health, policy, spec, now = inputs(tmp_path)
    assert (
        evaluate(item, state, health, replace(policy, **{field: boundary}), spec, now).action
        == "APPROVE"
    )
    decision = evaluate(item, state, health, replace(policy, **{field: below}), spec, now)
    assert decision.action == "REJECT" and reason in decision.reasons


@pytest.mark.parametrize(
    "kind", ["stale", "clock", "margin", "source", "currency", "liquidity", "step"]
)
def test_fail_closed_conditions(tmp_path, kind):
    _, _, item, state, health, policy, spec, now = inputs(tmp_path)
    if kind == "stale":
        now = pd.Timestamp(now) + pd.Timedelta(days=1)
    elif kind == "clock":
        now = pd.Timestamp(now) - pd.Timedelta(minutes=1)
    elif kind == "margin":
        policy = replace(policy, margin_proxy="unknown")
    elif kind == "source":
        health = replace(health, source_health="HTTP_403")
    elif kind == "currency":
        policy = replace(policy, currency="EUR")
    elif kind == "liquidity":
        health = replace(health, costs=replace(health.costs, participation_cap="0.00001"))
    else:
        item = {**item, "quantity": "1.5"}
    assert evaluate(item, state, health, policy, spec, now).action in ("REJECT", "HALT")


def test_latched_halt_immutable_policy_and_explicit_healthy_reset(tmp_path):
    store, key, _, _, health, policy, spec, now = inputs(tmp_path)
    engine = PaperRiskEngine(store)
    assert engine.assess("test", key, health, policy, spec, now).action == "APPROVE"
    with pytest.raises(ValueError, match="version conflict"):
        engine.pin_policy(replace(policy, max_open_contracts="5"), now)
    bad = replace(health, source_health="HTTP_429")
    assert engine.assess("test", key, bad, policy, spec, now).action == "HALT"
    assert PaperStore(store.path).inspect("test")["halted"]
    with pytest.raises(ValueError):
        engine.reset("test", policy, bad, spec, now, "operator reviewed")
    engine.reset("test", policy, health, spec, now, "operator reviewed fresh source")
    assert not store.inspect("test")["halted"]
    assert store.reconcile("test")


def test_daily_loss_drawdown_and_time_rollback(tmp_path):
    _, _, item, state, health, policy, spec, now = inputs(tmp_path)
    state["cash_events"].append({"reason": "FEE", "amount": "-100", "occurred_at": now})
    assert "DAILY_LOSS_LIMIT" in evaluate(item, state, health, policy, spec, now).reasons
    state["cash_events"].pop()
    state["ledger"]["cash"] = "800"
    assert "DRAWDOWN_LIMIT" in evaluate(item, state, health, policy, spec, now).reasons
    state["marks"].append({"timestamp": "2030-01-01T00:00:00Z", "data": {"equity": "1000"}})
    assert "CLOCK_ROLLBACK" in evaluate(item, state, health, policy, spec, now).reasons
