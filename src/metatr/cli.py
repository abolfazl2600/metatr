"""CLI entry point. Read-only: does not create or submit trading orders."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from metatr.data import TIMEFRAMES, csv_bars, mt5_bars, sample_bars
from metatr.strategy import ema_cross_signal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only MT5 signal research starter")
    parser.add_argument("--source", choices=("sample", "csv", "mt5"), default="sample")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAMES), default="M15")
    parser.add_argument("--csv", type=Path, help="CSV path (required for --source csv)")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--fast", type=int, default=20)
    parser.add_argument("--slow", type=int, default=50)
    parser.add_argument("--output", type=Path, help="Optional local JSON output")
    args = parser.parse_args(argv)

    if args.source == "sample":
        bars = sample_bars(args.count)
    elif args.source == "csv":
        if args.csv is None:
            parser.error("--csv is required for --source csv")
        bars = csv_bars(args.csv)
    else:
        bars = mt5_bars(args.symbol, args.timeframe, args.count)

    signal = ema_cross_signal(
        bars,
        symbol=args.symbol,
        timeframe=args.timeframe,
        source=args.source,
        fast=args.fast,
        slow=args.slow,
    )
    payload = json.dumps(signal.as_dict(), ensure_ascii=False, indent=2)
    print(payload)
    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
