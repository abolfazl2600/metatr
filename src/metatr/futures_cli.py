"""Read-only Coinbase Bitcoin futures discovery and per-market research CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from metatr.exchanges.coinbase_futures import (
    GRANULARITIES,
    CoinbaseFuturesFeed,
    validate_contract_metadata,
)
from metatr.futures_core import Market, load_configuration, select_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coinbase BTC futures (read-only)")
    parser.add_argument("--list", action="store_true", help="Discover actual BTC futures IDs")
    parser.add_argument("--product-id", help="Verified futures product ID (never BTC-USD spot)")
    parser.add_argument("--probe-candles", action="store_true",
                        help="Inspect live futures M15 candles, without model selection or orders")
    parser.add_argument("--timeframe", choices=sorted(GRANULARITIES), default="M15")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--config", type=Path, default=Path("config/markets.json"))
    parser.add_argument("--output", type=Path, help="Optional JSON signal for future MT5 display")
    args = parser.parse_args(argv)
    if not args.list and not args.product_id:
        parser.error("Use --list first, then supply a verified --product-id")
    if args.list and args.probe_candles:
        parser.error("--list and --probe-candles cannot be combined")
    feed = CoinbaseFuturesFeed()
    try:
        if args.list:
            items = feed.list_bitcoin_futures()
            output = {"exchange": "coinbase", "instrument": "FUTURE", "asset": "BTC",
                      "products": [{"product_id": p["product_id"],
                                    "display_name": p.get("display_name"),
                                    "details": p.get("future_product_details", {})} for p in items]}
        else:
            bars = feed.closed_candles(args.product_id, args.timeframe, args.count)
            if args.probe_candles:
                product = feed.require_bitcoin_future(args.product_id)
                output = {
                    "mode": "PUBLIC_DATA_PROBE",
                    "exchange": "coinbase",
                    "product_id": args.product_id,
                    "contract_kind": validate_contract_metadata(product),
                    "source": "coinbase_public_futures",
                    "timeframe": args.timeframe,
                    "count": len(bars),
                    "first_candle_utc": bars.index[0].isoformat(),
                    "last_candle_utc": bars.index[-1].isoformat(),
                    "validated_contiguous": True,
                    "spot_substitution": False,
                    "execution": "DISABLED",
                }
            else:
                market = Market("coinbase", "BTC", "FUTURE", args.product_id, args.timeframe)
                output = select_model(bars, market, load_configuration(args.config))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Futures data error: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(output, indent=2, ensure_ascii=False)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_suffix(args.output.suffix + ".tmp")
        temp.write_text(rendered + "\n", encoding="utf-8")
        temp.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
