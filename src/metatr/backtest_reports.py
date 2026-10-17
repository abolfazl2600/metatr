"""Auditable net-equity metrics and atomic immutable research report exports."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import shutil
import statistics
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from metatr.backtest import BacktestEngine
from metatr.contract_economics import decimal, number
from metatr.research import canonical, code_revision, digest
from metatr.validation import slice_dataset


def metrics(result: dict, annual_bars: int) -> dict:
    if type(annual_bars) is not int or annual_bars <= 0:
        raise ValueError("Explicit positive annual bar frequency required")
    marks = result["equity"]
    initial = decimal(result["risk"]["initial_equity"])
    values = [initial] + [decimal(m["equity"]) for m in marks]
    valid = bool(marks) and min(values) > 0 and result["status"] == "COMPLETE"
    returns = (
        [float(b / a - 1) for a, b in zip(values[:-1], values[1:], strict=True)] if valid else []
    )
    mean = statistics.mean(returns) if returns else None
    volatility = statistics.stdev(returns) if len(returns) > 1 else None
    downside = math.sqrt(sum(min(0, r) ** 2 for r in returns) / len(returns)) if returns else None
    peak, duration, max_duration, drawdown = initial, 0, 0, 0.0
    for value in values[1:]:
        if value >= peak:
            peak, duration = value, 0
        else:
            duration += 1
            max_duration = max(max_duration, duration)
        drawdown = max(drawdown, float((peak - value) / peak))
    pnls = [decimal(t["net_pnl"]) for t in result["trades"]]
    gains, losses = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
    fees = decimal(result["ledger"]["fees"])
    funding = decimal(result["ledger"]["funding"])
    return {
        "trade_count": len(pnls),
        "initial_equity": number(initial),
        "ending_equity": number(values[-1]),
        "net_pnl": number(values[-1] - initial),
        "net_return": float(values[-1] / initial - 1) if valid else None,
        "average_net_trade": number(sum(pnls) / len(pnls)) if pnls else None,
        "sharpe": mean / volatility * math.sqrt(annual_bars) if volatility else None,
        "sortino": mean / downside * math.sqrt(annual_bars) if downside else None,
        "annual_bars": annual_bars,
        "max_drawdown": drawdown if valid else None,
        "max_drawdown_duration_bars": max_duration if valid else None,
        "profit_factor": float(gains / losses) if losses else None,
        "exposure_fraction": sum(decimal(m["contracts"]) != 0 for m in marks) / len(marks)
        if marks
        else None,
        "turnover": float(sum(decimal(f["notional"]) for f in result["fills"]) / initial),
        "fees": number(fees),
        "funding": number(funding),
        "fee_burden": float((fees + funding) / initial),
        "wins": sum(p > 0 for p in pnls),
        "losses": sum(p < 0 for p in pnls),
        "breakeven": sum(p == 0 for p in pnls),
        "win_rate": sum(p > 0 for p in pnls) / len(pnls) if pnls else None,
        "net_trade_distribution": [number(p) for p in pnls],
        "inconclusive": not valid or not pnls or decimal(result["ledger"]["contracts"]) != 0,
    }


def _csv(rows: list[dict]) -> str:
    fields = sorted({key for row in rows for key in row})
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue()


@dataclass(frozen=True)
class BacktestReport:
    report_json: str
    trades_csv: str
    equity_csv: str

    def to_dict(self):
        return json.loads(self.report_json)


def build_report(
    validation: dict,
    dataset,
    candidates,
    contract,
    costs,
    risk,
    *,
    annual_bars: int,
    stress_scenarios=(),
) -> BacktestReport:
    dataset.verify()
    risk = replace(risk, max_holding_bars=validation["config"]["holding_horizon"])
    core_validation = {k: v for k, v in validation.items() if k != "validation_id"}
    if (
        digest(core_validation) != validation["validation_id"]
        or validation["data_sha256"] != dataset.data_sha256
    ):
        raise ValueError("Validation/report dataset mismatch")
    folds, trade_rows, equity_rows, stress = [], [], [], []
    for fold in validation["folds"]:
        test_metrics = metrics(fold["test"], annual_bars)
        baseline_metrics = {
            name: metrics(run, annual_bars) for name, run in fold["baselines"].items()
        }
        deltas = {
            name: (
                test_metrics["net_return"] - value["net_return"]
                if test_metrics["net_return"] is not None and value["net_return"] is not None
                else None
            )
            for name, value in baseline_metrics.items()
        }
        folds.append(
            {
                "fold": fold["fold"],
                "test": test_metrics,
                "benchmarks": baseline_metrics,
                "benchmark_net_return_deltas": deltas,
                "candidate_metrics": [
                    {
                        "config_sha256": a["strategy"]["config_sha256"],
                        "train": metrics(a["train"], annual_bars),
                        "validation": metrics(a["validation"], annual_bars)
                        if a["validation"]
                        else None,
                    }
                    for a in fold["candidate_audit"]
                ],
            }
        )
        for key, destination in (("trades", trade_rows), ("equity", equity_rows)):
            destination.extend(
                {"fold_id": fold["fold"]["fold_id"], **row} for row in fold["test"][key]
            )
        spec = next(
            (
                s
                for s in candidates
                if s.config_sha256 == fold["selected_strategy"]["config_sha256"]
            ),
            None,
        )
        if spec is None:
            raise ValueError("Selected strategy missing from pinned candidates")
        for scenario in stress_scenarios:
            if any(
                decimal(getattr(scenario, field)) < decimal(getattr(costs, field))
                for field in (
                    "taker_fee_bps",
                    "spread_bps",
                    "slippage_ticks",
                    "funding_bps_per_bar",
                )
            ) or decimal(scenario.participation_cap) > decimal(costs.participation_cap):
                raise ValueError(
                    "Stress costs must be at least as conservative as primary scenario"
                )
            # Freeze the primary selected model and exact OOS fold: never reselect on stress data.
            warmup = max(s.warmup_bars for s in candidates)
            start, end = fold["fold"]["test_start"], fold["fold"]["test_end"]
            prefix = max(0, start - warmup)
            run = (
                BacktestEngine()
                .run(
                    slice_dataset(dataset, prefix, end),
                    spec,
                    contract,
                    scenario,
                    risk,
                    validation["seed"],
                    decision_start=start - prefix,
                    decision_end=end - prefix - 1,
                )
                .to_dict()
            )
            stress.append(
                {
                    "fold_id": fold["fold"]["fold_id"],
                    "config_sha256": spec.config_sha256,
                    "scenario": asdict(scenario),
                    "scenario_sha256": scenario.fingerprint,
                    "metrics": metrics(run, annual_bars),
                    "result": run,
                }
            )
    initial = sum(decimal(f["test"]["initial_equity"]) for f in folds)
    net = sum(decimal(f["test"]["net_pnl"]) for f in folds)
    trades_csv, equity_csv = _csv(trade_rows), _csv(equity_rows)
    artifacts = {
        "trades.csv": hashlib.sha256(trades_csv.encode()).hexdigest(),
        "equity.csv": hashlib.sha256(equity_csv.encode()).hexdigest(),
    }
    core = {
        "schema_version": 1,
        "research_only": True,
        "execution": "DISABLED",
        "action": "NO_TRADE",
        "backtest_validated": False,
        "status": "INCONCLUSIVE" if validation["inconclusive"] else "RESEARCH_COMPLETE",
        "code_revision": code_revision(),
        "python_version": platform.python_version(),
        "package_versions": {
            name: importlib.metadata.version(name) for name in ("metatr-signal", "pandas", "numpy")
        },
        "market": dataset.market.key,
        "start_utc": dataset.start_utc,
        "end_utc_exclusive": dataset.end_utc,
        "asof_utc": dataset.asof_utc,
        "data_sha256": dataset.data_sha256,
        "provenance": dataset.provenance,
        "dataset_validation": json.loads(dataset.validation_json),
        "contract": asdict(contract),
        "cost_scenario": asdict(costs),
        "risk": asdict(risk),
        "validation": validation,
        "fold_metrics": folds,
        "cost_stress": stress,
        "aggregate_oos": {
            "policy": "independent fold capital, summed PnL / summed initial equity",
            "initial_equity_sum": number(initial),
            "net_pnl_sum": number(net),
            "equity_weighted_net_return": float(net / initial) if initial else None,
            "sharpe": None,
            "sortino": None,
            "ratio_reason": "independent fold resets; do not splice equity paths",
        },
        "artifacts_sha256": artifacts,
        "limitations": [
            "hypothetical OHLC liquidity; no L2",
            "assumed funding",
            "no verified margin/liquidation",
            "multiple candidate selection",
            "positive backtests do not guarantee live profitability",
        ],
    }
    return BacktestReport(canonical({"run_id": digest(core), **core}), trades_csv, equity_csv)


def export_report(report: BacktestReport, output: Path) -> Path:
    body = report.to_dict()
    if digest({k: v for k, v in body.items() if k != "run_id"}) != body["run_id"]:
        raise ValueError("Report integrity mismatch")
    contents = {
        "manifest.json": report.report_json + "\n",
        "trades.csv": report.trades_csv,
        "equity.csv": report.equity_csv,
    }
    for name, checksum in body["artifacts_sha256"].items():
        if hashlib.sha256(contents[name].encode()).hexdigest() != checksum:
            raise ValueError("CSV checksum mismatch")
    output.mkdir(parents=True, exist_ok=True)
    target = output / body["run_id"]
    if target.exists():
        if any(
            not (target / n).is_file() or (target / n).read_text() != c for n, c in contents.items()
        ):
            raise ValueError("Immutable report directory conflict")
        return target
    temp = Path(tempfile.mkdtemp(dir=output, prefix=".report-"))
    try:
        for name, content in contents.items():
            with (temp / name).open("w") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.rename(temp, target)
        except OSError:
            if not target.exists() or any(
                (target / n).read_text() != c for n, c in contents.items()
            ):
                raise
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return target
