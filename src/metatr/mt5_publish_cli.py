"""Publish verified or explicitly synthetic outbound files; no MT5 Python bindings."""

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from metatr.calendars import SessionCalendar
from metatr.contract_economics import ContractSpec
from metatr.dataset_quality import validate_dataset
from metatr.display_fixture import fixture_dataset
from metatr.futures_store import FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry
from metatr.mt5_publisher import publish
from metatr.research import parse_market, replay
from metatr.strategies import StrategyDecision


def main(argv=None):
    parser = argparse.ArgumentParser(description="One-way EXTERNAL/SIMULATED MT5 display publisher")
    parser.add_argument("--market", required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="Wine FILE_COMMON root")
    parser.add_argument("--fixture", action="store_true", help="Explicitly synthetic offline demo")
    parser.add_argument("--now-utc")
    for name in (
        "store",
        "registry",
        "strategy-id",
        "strategy-version",
        "start-utc",
        "end-utc",
        "asof-utc",
        "contract-spec",
        "calendar",
    ):
        parser.add_argument(f"--{name}")
    args = parser.parse_args(argv)
    try:
        market = parse_market(args.market)
        now = args.now_utc or datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as temporary:
            if args.fixture:
                dataset, spec, contract = fixture_dataset(market, now, Path(temporary))
            else:
                if any(
                    getattr(args, name) is None
                    for name in (
                        "store",
                        "registry",
                        "strategy_id",
                        "strategy_version",
                        "start_utc",
                        "end_utc",
                        "asof_utc",
                        "contract_spec",
                        "calendar",
                    )
                ):
                    raise ValueError(
                        "All exact stored-data/registry/economic/calendar pins required"
                    )
                if not Path(args.store).is_file() or not Path(args.registry).is_file():
                    raise ValueError("Existing store and registry required")
                body = json.loads(Path(args.contract_spec).read_text())
                body["market"] = parse_market(body["market"])
                contract = ContractSpec(**body)
                calendar = json.loads(Path(args.calendar).read_text())
                calendar["market"] = parse_market(calendar["market"])
                calendar["closures"] = tuple(tuple(w) for w in calendar.get("closures", []))
                report, dataset = validate_dataset(
                    FuturesSQLiteStore(args.store),
                    market,
                    args.start_utc,
                    args.end_utc,
                    args.asof_utc,
                    calendar=SessionCalendar(**calendar),
                )
                if not report.ready:
                    raise ValueError(f"Dataset not ready: {report.issues}")
                spec = StrategyRegistry(args.registry).resolve(
                    market, args.strategy_id, args.strategy_version
                )
            result = replay(dataset, spec)
            decisions = [StrategyDecision(**d) for d in result["decisions"]]
            print(publish(dataset, decisions, spec, contract, args.output_dir, now_utc=now))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"Display INVALID/STALE (NO_TRADE): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
