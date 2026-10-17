"""Chronological, purged/embargoed walk-forward validation; no test-set tuning."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from decimal import Decimal

from metatr.backtest import BacktestEngine, SimulationRisk
from metatr.contract_economics import decimal
from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.research import ResearchDataset, canonical, digest


@dataclass(frozen=True)
class FoldConfig:
    train_bars: int
    validation_bars: int
    test_bars: int
    step_bars: int
    purge_bars: int
    embargo_bars: int
    holding_horizon: int
    mode: str = "rolling"
    minimum_oos_folds: int = 2


@dataclass(frozen=True)
class Fold:
    fold_id: str
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int


def plan_folds(dataset, config: FoldConfig, warmup: int) -> tuple[Fold, ...]:
    dataset.verify()
    if (any(type(v) is not int for k, v in asdict(config).items() if k != "mode")
            or config.mode not in ("rolling", "anchored") or config.train_bars < warmup + 3
            or config.validation_bars < 0
            or (config.validation_bars and config.validation_bars < 3)
            or config.test_bars < 3 or config.step_bars < config.test_bars
            or config.minimum_oos_folds < 2 or config.holding_horizon < 1
            or min(config.purge_bars, config.embargo_bars) < max(warmup, config.holding_horizon)):
        raise ValueError("Unsafe temporal split, warmup, overlapping test, purge or embargo")
    bars = dataset.bars
    seconds = TIMEFRAME_SECONDS[dataset.market.timeframe]
    if ((bars.index.asi8[1:] - bars.index.asi8[:-1]) != seconds * 10**9).any():
        raise ValueError("Session-discontinuous folds are unsupported; no contract stitching")
    folds = []
    end = config.train_bars
    while True:
        train_start = 0 if config.mode == "anchored" else end - config.train_bars
        validation_start = end + config.purge_bars
        validation_end = validation_start + config.validation_bars
        test_start = validation_end + config.embargo_bars
        test_end = test_start + config.test_bars
        if test_end > len(bars):
            break
        body = {"market": dataset.market.key, "config": asdict(config),
                "train_start": train_start, "train_end": end,
                "validation_start": validation_start, "validation_end": validation_end,
                "test_start": test_start, "test_end": test_end}
        folds.append(Fold(digest(body), train_start, end, validation_start,
                          validation_end, test_start, test_end))
        end += config.step_bars
    if len(folds) < config.minimum_oos_folds:
        raise ValueError("Insufficient independent out-of-sample windows")
    return tuple(folds)


def slice_dataset(dataset: ResearchDataset, start: int, end: int) -> ResearchDataset:
    """Derive an intact contiguous subset; provenance stays pinned to parent captures."""
    dataset.verify()
    bars = dataset.bars
    report = json.loads(dataset.validation_json)
    if not report or not report["ready"] or not 0 <= start < end <= len(bars):
        raise ValueError("Invalid validated subset")
    report_sha = report.pop("report_sha256")
    if digest(report) != report_sha:
        raise ValueError("Invalid parent validation report")
    records = json.loads(dataset.bars_json)[start:end]
    start_utc = bars.index[start].isoformat()
    end_utc = (bars.index[end].isoformat() if end < len(bars) else dataset.end_utc)
    data_hash = digest({"market": dataset.market.key, "bars": records,
                        "provenance": dataset.provenance})
    report.update(start_utc=start_utc, end_utc_exclusive=end_utc, data_sha256=data_hash,
                  parent_data_sha256=dataset.data_sha256)
    report["report_sha256"] = digest(report)
    return replace(dataset, bars_json=canonical(records), start_utc=start_utc, end_utc=end_utc,
                   data_sha256=data_hash, validation_json=canonical(report))


def walk_forward(dataset, candidates, contract_spec, cost_scenario,
                 risk_config: SimulationRisk, config: FoldConfig, seed=0) -> dict:
    if not candidates or any(s.market != dataset.market or s.status == "retired"
                             for s in candidates):
        raise ValueError("Exact-market active candidate versions required")
    if len({s.config_sha256 for s in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate configuration")
    warmup = max(s.warmup_bars for s in candidates)
    folds = plan_folds(dataset, config, warmup)
    risk = replace(risk_config, max_holding_bars=config.holding_horizon)
    engine = BacktestEngine()

    def simulate(spec, start, end, baseline=None):
        prefix = max(0, start - warmup)
        subset = slice_dataset(dataset, prefix, end)
        return engine.run(subset, spec, contract_spec, cost_scenario, risk, seed,
                          decision_start=start - prefix, decision_end=end - prefix - 1,
                          baseline=baseline).to_dict()

    results = []
    for fold in folds:
        audit = []
        for spec in candidates:
            train = simulate(spec, fold.train_start + warmup, fold.train_end)
            validation = (simulate(spec, fold.validation_start, fold.validation_end)
                          if config.validation_bars else None)
            selecting = validation or train
            eligible = (selecting["status"] == "COMPLETE"
                        and decimal(selecting["ledger"]["contracts"]) == 0)
            score = decimal(selecting["equity"][-1]["equity"]) if eligible else Decimal("-Infinity")
            audit.append({"strategy": spec.to_dict(), "train": train, "validation": validation,
                          "selection_score": str(score) if eligible else None,
                          "eligible": eligible})
        eligible = [(decimal(a["selection_score"]), a["strategy"]["config_sha256"], i)
                    for i, a in enumerate(audit) if a["eligible"]]
        if not eligible:
            raise ValueError("No settled/complete training candidate; OOS blocked")
        winner = max(eligible)[2]
        frozen = candidates[winner]
        test = simulate(frozen, fold.test_start, fold.test_end)
        baselines = {name: simulate(frozen, fold.test_start, fold.test_end, name)
                     for name in ("flat", "buy_hold")}
        inconclusive = (not test["trades"] or test["status"] != "COMPLETE"
                        or decimal(test["ledger"]["contracts"]) != 0)
        results.append({"fold": asdict(fold), "candidate_audit": audit,
                        "selected_strategy": frozen.to_dict(), "test": test,
                        "baselines": baselines, "inconclusive": inconclusive})
    core = {"schema_version": 1, "market": dataset.market.key, "data_sha256": dataset.data_sha256,
            "config": asdict(config), "seed": seed, "candidate_count": len(candidates),
            "selection_policy": "validation equity if present; otherwise training equity",
            "fold_policy": "independent initial equity; next-bar settlement inside each fold",
            "multiple_testing_warning": "Preset candidates; no statistical profitability proof",
            "research_only": True, "execution": "DISABLED", "action": "NO_TRADE",
            "inconclusive": any(f["inconclusive"] for f in results), "folds": results}
    return {"validation_id": digest(core), **core}
