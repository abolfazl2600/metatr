"""Offline CLI for explicit, research-only futures strategy registration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from metatr.futures_core import Market
from metatr.model_registry import (
    DEFAULT_REGISTRY_PATH,
    RegistryError,
    StrategyRegistry,
    import_legacy_config,
)


def _add_market(parser: argparse.ArgumentParser, *, required: bool) -> None:
    for name in ("exchange", "asset", "instrument", "product-id", "timeframe"):
        parser.add_argument(f"--{name}", required=required)


def _market(args: argparse.Namespace, *, optional: bool = False) -> Market | None:
    names = ("exchange", "asset", "instrument", "product_id", "timeframe")
    values = [getattr(args, name) for name in names]
    if optional and not any(values):
        return None
    if any(value is None for value in values):
        raise RegistryError("Provide all five market identity fields, or none")
    return Market(*values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Versioned strategy registry (no execution)")
    parser.add_argument("--db", type=Path, default=DEFAULT_REGISTRY_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser(
        "list", help="List registered models, optionally for one exact market"
    )
    _add_market(listing, required=False)
    listing.add_argument("--status", choices=("draft", "research", "retired"))
    importing = sub.add_parser(
        "import-legacy", help="Import models for an explicit native contract"
    )
    _add_market(importing, required=True)
    importing.add_argument("--config", type=Path, default=Path("config/markets.json"))
    importing.add_argument("--allow-wildcard", action="store_true",
                           help="Explicitly use an exchange/asset/instrument/timeframe template")
    importing.add_argument("--version", default="1.0.0")
    for name in ("show", "retire"):
        action = sub.add_parser(name, help=f"{name.title()} an exact pinned version")
        _add_market(action, required=True)
        action.add_argument("--strategy-id", required=True)
        action.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        registry = StrategyRegistry(args.db)
        if args.command == "list":
            specs = registry.list(_market(args, optional=True), status=args.status)
        elif args.command == "import-legacy":
            specs = import_legacy_config(registry, _market(args), args.config,
                                         allow_wildcard=args.allow_wildcard,
                                         version=args.version)
        elif args.command == "show":
            specs = [registry.resolve(_market(args), args.strategy_id, args.version)]
        else:
            specs = [registry.retire(_market(args), args.strategy_id, args.version)]
        print(json.dumps([spec.to_dict() for spec in specs], indent=2,
                         ensure_ascii=False, sort_keys=True))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Registry error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
