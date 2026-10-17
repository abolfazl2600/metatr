"""Reproducible store/research/display handoff; native visual evidence is separate."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from metatr.calendars import SessionCalendar
from metatr.contract_economics import ContractSpec
from metatr.dataset_quality import validate_dataset
from metatr.display_fixture import fixture_dataset
from metatr.exchanges.coinbase_futures import CoinbaseFuturesFeed
from metatr.futures_store import TIMEFRAME_SECONDS, FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry, StrategySpec
from metatr.mt5_publisher import publish, read_publication
from metatr.research import digest, parse_market, replay, write_experiment
from metatr.strategies import StrategyDecision, utc


def verify_handoff(dataset, decisions, spec, contract, folder, now):
    """Compare independently decoded CSV rows to the SQLite-derived dataset/replay."""
    result = read_publication(folder, dataset.market, now_utc=now)
    manifest = result["manifest"]
    expected = {
        "data_sha256": dataset.data_sha256,
        "contract_sha256": contract.fingerprint,
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.version,
        "config_sha256": spec.config_sha256,
        "status": "VALID",
    }
    if any(manifest[k] != v for k, v in expected.items()):
        raise ValueError("Handoff provenance differs from research inputs")
    if len(result["bars"]) != len(dataset.bars) or len(result["signals"]) != len(decisions):
        raise ValueError("Handoff row count differs from research inputs")
    for (opened, values), row in zip(dataset.bars.iterrows(), result["bars"], strict=True):
        if int(row["timestamp"]) != int(opened.timestamp()) or any(
            Decimal(row[c]) != Decimal(str(values[c])) for c in values.index
        ):
            raise ValueError("Handoff candle UTC/OHLCV mismatch")
    for decision, row in zip(decisions, result["signals"], strict=True):
        if (
            int(row["bar_open_utc"]) != int(utc(decision.bar_open_utc).timestamp())
            or int(row["decision_utc"]) != int(utc(decision.timestamp).timestamp())
            or row["action"] != decision.action
        ):
            raise ValueError("Handoff signal/action/time mismatch")
    return manifest


def run_pipeline(market, workspace, output_dir, now, *, live=False, contract_path=None,
                 calendar_path=None, strategy_id="momentum"):
    if market.product_id != "BIP-20DEC30-CDE" or market.timeframe != "M15":
        raise ValueError("Integration harness requires exact BIP-20DEC30-CDE M15 market")
    if live and (contract_path is None or calendar_path is None):
        raise ValueError("Live capture requires sourced --contract-spec and --calendar")
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    if live:
        # Explicit public-network opt-in only. Never fall back to synthetic prices.
        body = json.loads(Path(contract_path).read_text())
        body["market"] = parse_market(body["market"])
        contract = ContractSpec(**body)
        body = json.loads(Path(calendar_path).read_text())
        body["market"] = parse_market(body["market"])
        body["closures"] = tuple(tuple(w) for w in body.get("closures", []))
        calendar = SessionCalendar(**body)
        if contract.market != market or calendar.market != market:
            raise ValueError("Live contract/calendar market mismatch")
        capture = CoinbaseFuturesFeed().capture_candles(market.product_id, market.timeframe, 80)
        now = capture.captured_at
        store = FuturesSQLiteStore(workspace / "public.sqlite")
        store.save_snapshot(market, source="coinbase_public_futures",
                            raw_payload=capture.raw_payload, contract_metadata=capture.product,
                            bars=capture.bars, asof=now)
        start = capture.bars.index[0]
        end = capture.bars.index[-1] + pd.Timedelta(seconds=TIMEFRAME_SECONDS[market.timeframe])
        quality, dataset = validate_dataset(store, market, start, end, now, calendar=calendar)
        if not quality.ready:
            raise ValueError(f"Public dataset not ready: {quality.issues}")
    else:
        fixture = workspace / "fixtures" / digest(utc(now).floor("15min").isoformat())[:16]
        fixture.mkdir(parents=True, exist_ok=True)
        dataset, _, contract = fixture_dataset(market, now, fixture)
    registry = StrategyRegistry(workspace / "registry.sqlite")
    specs = {}
    for name, params in (("momentum", {"lookback": 8, "threshold": 0.001}),
                         ("ema_cross", {"fast": 5, "slow": 20})):
        # Stable creation time makes repeated captures compatible with immutable registry pins.
        specs[name] = registry.register(StrategySpec.create(
            market, name, "1.0.0", name, params,
            created_at="2020-01-01T00:00:00Z", status="research"))
    runs = {}
    selected = None
    for name in specs:
        spec = registry.resolve(market, name, "1.0.0")
        result = replay(dataset, spec)
        write_experiment(result, workspace / "research")
        runs[name] = {"config_sha256": spec.config_sha256,
                      "decision_count": len(result["decisions"])}
        if name == strategy_id:
            selected = [StrategyDecision(**d) for d in result["decisions"]]
    spec = specs[strategy_id]
    manifest_path = publish(dataset, selected, spec, contract, output_dir, now_utc=now)
    manifest = verify_handoff(dataset, selected, spec, contract, manifest_path.parent, now)
    body = {
        "schema_version": 1, "mode": "PUBLIC_LIVE" if live else "SYNTHETIC_OFFLINE",
        "market": market.key, "asof_utc": dataset.asof_utc,
        "snapshot_ids": dataset.snapshot_ids, "data_sha256": dataset.data_sha256,
        "manifest": manifest, "strategies": runs,
        "handoff_verified": True, "native_mt5_acceptance": "PENDING_MANUAL_EVIDENCE",
        "execution": "DISABLED",
    }
    evidence = write_experiment({"run_id": digest(body), **body}, workspace / "handoff")
    return manifest_path, evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description="SQLite → research → MT5 file handoff")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture", action="store_true", help="Synthetic offline integration")
    mode.add_argument("--live", action="store_true", help="Opt in to public Coinbase network")
    parser.add_argument("--market", default="coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="FILE_COMMON root")
    parser.add_argument("--strategy-id", choices=("momentum", "ema_cross"), default="momentum")
    parser.add_argument("--now-utc", help="Deterministic fixture clock; forbidden for live capture")
    parser.add_argument("--contract-spec", type=Path)
    parser.add_argument("--calendar", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.live and args.now_utc:
            raise ValueError("Live capture uses its actual receipt time")
        if args.fixture and (args.contract_spec or args.calendar):
            raise ValueError("Fixture uses synthetic pins; omit live contract/calendar")
        manifest, evidence = run_pipeline(
            parse_market(args.market), args.workspace, args.output_dir,
            args.now_utc or datetime.now(timezone.utc).isoformat(), live=args.live,
            contract_path=args.contract_spec, calendar_path=args.calendar,
            strategy_id=args.strategy_id)
        print(json.dumps({"manifest": str(manifest), "evidence": str(evidence),
                          "native_acceptance": "PENDING", "execution": "DISABLED"}, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"Pipeline INVALID/STALE (NO_TRADE): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
