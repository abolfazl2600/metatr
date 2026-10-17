"""Read-only MQL5 FILE_COMMON snapshot adapter for macOS/Wine and other platforms."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from metatr.data import TIMEFRAMES, csv_bars
from metatr.strategy import Signal, ema_cross_signal

SCHEMA_VERSION = 1
BRIDGE_COLUMNS = ("symbol", "timeframe", "schema_version")


def analyze_bridge_snapshot(
    path: str | Path,
    *,
    symbol: str,
    timeframe: str,
    fast: int = 20,
    slow: int = 50,
) -> Signal:
    """Read one published, completed-bar snapshot and validate its identity."""
    bars = csv_bars(path)
    missing = set(BRIDGE_COLUMNS) - set(bars.columns)
    if missing:
        raise ValueError(f"Bridge CSV missing columns: {sorted(missing)}")
    if set(bars["symbol"].astype(str)) != {symbol}:
        raise ValueError(f"Bridge file does not contain only symbol {symbol!r}")
    if set(bars["timeframe"].astype(str)) != {timeframe}:
        raise ValueError(f"Bridge file does not contain only timeframe {timeframe!r}")
    if not bars["schema_version"].eq(SCHEMA_VERSION).all():
        raise ValueError(f"Unsupported bridge schema; expected version {SCHEMA_VERSION}")
    return ema_cross_signal(
        bars, symbol=symbol, timeframe=timeframe, source="bridge", fast=fast, slow=slow
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read closed-bar CSV snapshots published by the read-only MQL5 bridge"
    )
    parser.add_argument(
        "--file", required=True, type=Path, help="Absolute macOS path to FILE_COMMON CSV"
    )
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15", choices=sorted(TIMEFRAMES))
    parser.add_argument("--fast", default=20, type=int)
    parser.add_argument("--slow", default=50, type=int)
    parser.add_argument(
        "--watch", action="store_true", help="Wait for new closed candles and emit JSONL"
    )
    parser.add_argument("--interval", default=2.0, type=float, help="Polling interval in seconds")
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")

    if not args.watch:
        try:
            signal = analyze_bridge_snapshot(
                args.file, symbol=args.symbol, timeframe=args.timeframe,
                fast=args.fast, slow=args.slow,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Bridge error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(signal.as_dict(), ensure_ascii=False, indent=2))
        return 0

    print(
        f"Watching {args.file} for new completed {args.timeframe} candles (Ctrl+C to stop)",
        file=sys.stderr,
    )
    last_timestamp: str | None = None
    last_error: str | None = None
    try:
        while True:
            try:
                signal = analyze_bridge_snapshot(
                    args.file, symbol=args.symbol, timeframe=args.timeframe,
                    fast=args.fast, slow=args.slow,
                )
                last_error = None
                if signal.timestamp != last_timestamp:
                    print(json.dumps(signal.as_dict(), ensure_ascii=False), flush=True)
                    last_timestamp = signal.timestamp
            except (OSError, ValueError) as exc:
                # FILE_COMMON snapshot is published via rename; on a missing/incomplete
                # file, wait for the exporter to publish the next valid snapshot.
                description = f"Bridge waiting: {exc}"
                if description != last_error:
                    print(description, file=sys.stderr)
                    last_error = description
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
