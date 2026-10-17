"""Offline audited walk-forward reports with explicit pinned economics/config."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from metatr.backtest import SimulationRisk
from metatr.backtest_reports import build_report, export_report
from metatr.calendars import SessionCalendar
from metatr.contract_economics import ContractSpec, CostScenario
from metatr.dataset_quality import validate_dataset
from metatr.futures_store import FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry
from metatr.research import parse_market
from metatr.validation import FoldConfig, walk_forward


def load(path):
    return json.loads(Path(path).read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline hypothetical futures backtest report")
    for name in (
        "store",
        "registry",
        "market",
        "start-utc",
        "end-utc",
        "asof-utc",
        "output",
        "contract-spec",
        "cost-scenario",
        "risk-policy",
        "fold-config",
        "calendar",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--strategy", action="append", required=True, help="Pinned id@version")
    parser.add_argument("--annual-bars", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--stress-scenario", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if not Path(args.store).is_file() or not Path(args.registry).is_file():
            raise ValueError("Existing store/registry required")
        market = parse_market(args.market)
        registry = StrategyRegistry(args.registry)
        specs = [registry.resolve(market, *pin.split("@")) for pin in args.strategy]
        contract_body = load(args.contract_spec)
        contract_body["market"] = parse_market(contract_body["market"])
        contract = ContractSpec(**contract_body)
        costs = CostScenario(**load(args.cost_scenario))
        config = FoldConfig(**load(args.fold_config))
        risk_body = load(args.risk_policy)
        risk_body["max_holding_bars"] = config.holding_horizon
        risk = SimulationRisk(**risk_body)
        calendar_body = load(args.calendar)
        calendar_body["market"] = parse_market(calendar_body["market"])
        calendar_body["closures"] = tuple(tuple(w) for w in calendar_body.get("closures", []))
        report, dataset = validate_dataset(
            FuturesSQLiteStore(args.store),
            market,
            args.start_utc,
            args.end_utc,
            args.asof_utc,
            calendar=SessionCalendar(**calendar_body),
        )
        if not report.ready:
            raise ValueError(f"Dataset not ready: {report.issues}")
        validation = walk_forward(dataset, specs, contract, costs, risk, config, args.seed)
        result = build_report(
            validation,
            dataset,
            specs,
            contract,
            costs,
            risk,
            annual_bars=args.annual_bars,
            stress_scenarios=[CostScenario(**load(p)) for p in args.stress_scenario],
        )
        print(export_report(result, Path(args.output)))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"Backtest blocked (NO_TRADE): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
