"""Offline dataset readiness and missing-window planner; never auto-backfills."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from metatr.calendars import SessionCalendar
from metatr.dataset_quality import validate_dataset
from metatr.futures_store import FuturesSQLiteStore
from metatr.research import canonical, parse_market


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline dataset readiness (execution DISABLED)")
    for name in ("store", "market", "start-utc", "end-utc", "asof-utc"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--require-fresh", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not Path(args.store).is_file():
            raise ValueError("Existing candle store required")
        market = parse_market(args.market)
        calendar = None
        if args.calendar:
            body = json.loads(args.calendar.read_text())
            body["market"] = parse_market(body["market"])
            body["closures"] = tuple(tuple(window) for window in body.get("closures", []))
            calendar = SessionCalendar(**body)
        report, _ = validate_dataset(FuturesSQLiteStore(args.store), market, args.start_utc,
                                     args.end_utc, args.asof_utc, calendar=calendar,
                                     require_fresh=args.require_fresh)
        print(canonical(report.to_dict()))
        return 0 if report.ready else 1
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"Dataset error (NO_TRADE): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
