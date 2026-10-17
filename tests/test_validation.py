from dataclasses import replace

import pytest
from test_backtest import fixture

from metatr.model_registry import StrategySpec
from metatr.validation import FoldConfig, plan_folds, walk_forward


def inputs(tmp_path, suffix=0):
    values = tuple(10000 + (i % 7 - 3) * 10 if i < 40 else
                   10000 + (i % 5 - 2) * 10 + suffix for i in range(110))
    dataset, momentum, spec, costs, risk = fixture(tmp_path, values)
    ema = StrategySpec.create(momentum.market, "ema_cross", "1.0.0", "ema_cross",
                              {"fast": 1, "slow": 2}, created_at="2020-01-01T00:00:00Z")
    config = FoldConfig(30, 0, 20, 20, 4, 4, 2)
    return dataset, [momentum, ema], spec, costs, risk, config


def test_repeat_and_training_independent_of_oos(tmp_path):
    args = inputs(tmp_path)
    result = walk_forward(*args)
    assert result == walk_forward(*args)
    path = tmp_path / "modified"
    path.mkdir()
    modified = walk_forward(*inputs(path, suffix=20))
    assert result["folds"][0]["selected_strategy"] == modified["folds"][0]["selected_strategy"]
    for a, b in zip(result["folds"][0]["candidate_audit"],
                    modified["folds"][0]["candidate_audit"], strict=True):
        assert a["selection_score"] == b["selection_score"]
    assert all("flat" in f["baselines"] and "buy_hold" in f["baselines"] for f in result["folds"])
    assert result["action"] == "NO_TRADE"


@pytest.mark.parametrize("field,value", [("purge_bars", 0), ("embargo_bars", 1),
                                         ("step_bars", 2), ("train_bars", 3),
                                         ("holding_horizon", 5), ("test_bars", 1000)])
def test_unsafe_split_refused(tmp_path, field, value):
    dataset, _, _, _, _, config = inputs(tmp_path)
    with pytest.raises(ValueError):
        plan_folds(dataset, replace(config, **{field: value}), 4)


def test_anchored_boundaries_and_product_refusal(tmp_path):
    args = inputs(tmp_path)
    folds = plan_folds(args[0], replace(args[-1], mode="anchored"), 4)
    assert all(f.train_start == 0 for f in folds)
    assert folds[0].train_end + 8 == folds[0].test_start
    assert folds[1].test_start >= folds[0].test_end
    wrong = replace(args[1][0], market=replace(args[0].market, product_id="BIT-20DEC30-CDE"))
    with pytest.raises(ValueError):
        walk_forward(args[0], [wrong], *args[2:])
