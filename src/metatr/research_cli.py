"""Explicit offline-only stored-candle research entrypoint."""

import argparse
import sqlite3
import sys
from pathlib import Path

from metatr.futures_store import FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry
from metatr.research import parse_market, read_dataset, replay, write_experiment


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline signal replay; execution DISABLED")
    for name in ("store", "registry", "market", "strategy-id", "strategy-version",
                 "start-utc", "end-utc", "output"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--asof-utc")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--offline-only", action="store_true",
                        help="Explicitly acknowledge offline operation (always enforced)")
    args = parser.parse_args(argv)
    try:
        if not Path(args.store).is_file() or not Path(args.registry).is_file():
            raise ValueError("Existing store and registry files are required")
        market = parse_market(args.market)
        spec = StrategyRegistry(args.registry).resolve(market, args.strategy_id,
                                                       args.strategy_version)
        dataset = read_dataset(FuturesSQLiteStore(args.store), market, args.start_utc,
                               args.end_utc, asof_utc=args.asof_utc)
        result = replay(dataset, spec, args.seed)
        print(write_experiment(result, Path(args.output)))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"Research error (NO_TRADE): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
