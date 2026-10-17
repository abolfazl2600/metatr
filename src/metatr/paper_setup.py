"""Prepare a public-data PAPER scenario and optionally start its existing runner."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd

from metatr.calendars import SessionCalendar, stamp
from metatr.contract_economics import ContractSpec, CostScenario, native_contract_fingerprint
from metatr.dataset_quality import validate_dataset
from metatr.exchanges.coinbase_futures import CoinbaseFuturesFeed
from metatr.futures_store import FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry, StrategySpec
from metatr.paper.risk import PaperRiskPolicy
from metatr.paper.store import PaperStore
from metatr.paper_cli import main as paper_main
from metatr.research import canonical, digest, parse_market

MARKET = parse_market("coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15")
SOURCE = "https://api.coinbase.com/api/v3/brokerage/market/products/BIP-20DEC30-CDE"


def _body(value):
    body = asdict(value)
    if "market" in body:
        body["market"] = MARKET.key
    return body


def _capture_with_retry(feed, *, wait=time.sleep):
    """Retry transient public reads before creating any persistent Paper state.

    Deliberately do not retry 403/404/429: they require operator review and
    must never be interpreted as permission to fall back to another market.
    No private/authenticated endpoints are used.
    """
    for attempt in range(3):
        try:
            return feed.capture_candles(MARKET.product_id, "M15", 80)
        except HTTPError as exc:
            if exc.code not in (500, 502, 503, 504) or attempt == 2:
                raise ValueError(
                    f"PUBLIC_HTTP_{exc.code}: Coinbase product/candles unavailable"
                ) from None
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise ValueError(
                    "PUBLIC_SOURCE_CONNECTION_FAILED: three public Coinbase requests failed; "
                    "check Mac network/VPN/proxy and test the exact product URL with curl"
                ) from None
        wait(2**attempt)
    raise AssertionError("Unreachable bounded retry state")


def prepare(workspace, *, feed=None):
    """Publish a complete new profile atomically. Never edit an existing account."""
    workspace = Path(workspace).expanduser().resolve()
    if workspace.exists():
        raise ValueError("Workspace already exists; resume it with --start, do not overwrite")
    capture = _capture_with_retry(feed or CoinbaseFuturesFeed())
    now = stamp(capture.captured_at)
    end = capture.bars.index[-1] + pd.Timedelta(minutes=15)
    if not end <= now < end + pd.Timedelta(minutes=30):
        raise ValueError("Public capture is stale or contains an unclosed candle")
    meta = capture.product
    session = meta.get("fcm_trading_session_details", {})
    if not session.get("is_session_open") or session.get("session_state") != (
        "FCM_TRADING_SESSION_STATE_OPEN"
    ):
        raise ValueError("Public venue does not confirm an open session")
    opened, closed = stamp(session["open_time"]), stamp(session["close_time"])
    first = capture.bars.index[0]
    # Bounded to this actual API session, never guess future weekends/holidays.
    if first < opened or not opened <= now < closed:
        raise ValueError("Capture/warmup outside confirmed session; retry in a covered interval")
    for value in (opened, closed):
        if value.value % (900 * 10**9):
            raise ValueError("Session boundary is not M15 aligned")
    closures = []
    maintenance = session.get("maintenance")
    if maintenance:
        left, right = stamp(maintenance["start_time"]), stamp(maintenance["end_time"])
        if left >= right:
            raise ValueError("Invalid announced maintenance window")
        if left < closed and right > opened:
            closures.append((max(left, opened).isoformat(), min(right, closed).isoformat()))
    calendar_version = digest({"open": opened.isoformat(), "close": closed.isoformat(),
                               "closures": closures})[:16]
    calendar = SessionCalendar(
        MARKET, "coinbase-confirmed-session", calendar_version, SOURCE,
        "UTC", opened.isoformat(), closed.isoformat(), tuple(closures))
    contract = ContractSpec(
        MARKET, "native-v1", "BIP", "PERPETUAL_STYLE", "BTC", "USD", "USD",
        str(meta["future_product_details"]["contract_size"]), str(meta["base_min_size"]),
        str(meta["base_increment"]), str(meta["price_increment"]), 2,
        calendar.calendar_id, calendar.version, "USD", end.isoformat(), SOURCE,
        native_contract_fingerprint(meta))
    contract.validate(meta, now)
    assumptions = (
        "PAPER hypothetical scenario, not Coinbase fee/funding/margin terms: "
        "10000 USD synthetic equity; fully collateralized; one contract target; "
        "10 bps maker/taker, 2 bps full spread, one tick slippage; assumed candle "
        "volume units=contracts with 10% participation; absolute 0.01 bps funding "
        "charge per M15 bar. No actual funding, margin or liquidation model."
    )
    costs = CostScenario(
        "paper-assumptions", "1.0.0", end.isoformat(), "WRITTEN_HYPOTHETICAL_SCENARIO",
        assumptions, "USD", "10", "10", "2", "1", "0.1", "contracts",
        "ASSUMED_ABSOLUTE_NOTIONAL_PER_BAR", "0.01")
    costs.validate(contract, now)
    policy = PaperRiskPolicy(
        "bip-paper", MARKET, "bounded-paper", "1.0.0", "10000", "USD", "4000",
        "2000", "1", "2000", "2000", "1", 1, 1, 0, "100", "0.05", 2,
        "ASSUMED_FULLY_COLLATERALIZED", assumptions)
    policy.validate()
    workspace.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".paper-setup-", dir=workspace.parent))
    try:
        os.chmod(stage, 0o700)
        store = FuturesSQLiteStore(stage / "market.sqlite")
        store.save_snapshot(MARKET, source="coinbase_public_futures",
                            raw_payload=capture.raw_payload, contract_metadata=meta,
                            bars=capture.bars, asof=now.to_pydatetime())
        quality, _ = validate_dataset(store, MARKET, first, end, now, calendar=calendar)
        if not quality.ready:
            raise ValueError(f"Public dataset not ready: {quality.issues}")
        registry = StrategyRegistry(stage / "registry.sqlite")
        registry.register(StrategySpec.create(
            MARKET, "momentum", "1.0.0", "momentum", {"lookback": 8, "threshold": 0.001},
            created_at=end.isoformat(), status="research"))
        PaperStore(stage / "paper.sqlite").initialize(
            policy.account, MARKET, policy.initial_equity, at_utc=now)
        for name, value in (("contract", contract), ("costs", costs),
                            ("calendar", calendar), ("risk", policy)):
            (stage / f"{name}.json").write_text(canonical(_body(value)) + "\n")
        (stage / "public-metadata.json").write_text(canonical(meta) + "\n")
        profile = {"schema_version": 1, "account": policy.account, "market": MARKET.key,
                   "label": "PAPER_ONLY_SIMULATED", "prepared_utc": now.isoformat(),
                   "session_coverage_end_utc": closed.isoformat(),
                   "native_metadata_scope": "NATIVE_CONTRACT_V1",
                   "contract_sha256": contract.fingerprint,
                   "cost_sha256": costs.fingerprint, "risk_sha256": policy.fingerprint,
                   "operational_acceptance": "PENDING_MONITORING", "assumptions": assumptions}
        (stage / "setup.json").write_text(canonical(profile) + "\n")
        os.rename(stage, workspace)
        return profile
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def runner_args(workspace, command, *, max_cycles=None, display_output_dir=None):
    workspace = Path(workspace).expanduser().resolve()
    profile = json.loads((workspace / "setup.json").read_text())
    if (profile["schema_version"] != 1 or profile["market"] != MARKET.key
            or profile["account"] != "bip-paper"):
        raise ValueError("Unknown setup profile")
    args = [command, "--paper-db", str(workspace / "paper.sqlite"),
            "--account", profile["account"], "--market", MARKET.key]
    if command == "run":
        args += ["--mode", "paper", "--confirm-paper", "--contracts", "1",
                 "--store", str(workspace / "market.sqlite"),
                 "--registry", str(workspace / "registry.sqlite"),
                 "--strategy-id", "momentum", "--strategy-version", "1.0.0",
                 "--count", "80", "--poll-seconds", "30", "--max-catch-up-bars", "80",
                 "--audit-log", str(workspace / "audit.jsonl"),
                 "--observation-workspace", str(workspace)]
        for flag, name in (("contract-spec", "contract"), ("cost-scenario", "costs"),
                           ("calendar", "calendar"), ("risk-policy", "risk")):
            args += [f"--{flag}", str(workspace / f"{name}.json")]
        if max_cycles is not None:
            args += ["--max-cycles", str(max_cycles)]
        if display_output_dir:
            args += ["--display-output-dir", str(display_output_dir)]
    elif command == "report":
        args += ["--output", str(workspace / "reports")]
    return args


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare/start a bounded public PAPER scenario")
    parser.add_argument("--workspace", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--start", action="store_true",
                      help="Prepare if absent, then poll in foreground")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--report", action="store_true")
    parser.add_argument("--confirm-paper", action="store_true")
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--display-output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.start and not args.confirm_paper:
            raise ValueError("Starting requires --confirm-paper for the hypothetical scenario")
        if args.max_cycles is not None and (not args.start or args.max_cycles < 1):
            raise ValueError("--max-cycles requires --start and a positive bound")
        if args.display_output_dir and not args.start:
            raise ValueError("--display-output-dir requires --start")
        if args.status or args.report:
            return paper_main(runner_args(args.workspace, "report" if args.report else "status"))
        if not args.workspace.expanduser().exists():
            print(canonical(prepare(args.workspace)), flush=True)
        elif not args.start:
            raise ValueError("Workspace already exists; use --status or --start")
        if args.start:
            return paper_main(runner_args(args.workspace, "run", max_cycles=args.max_cycles,
                                          display_output_dir=args.display_output_dir))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"PAPER setup stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
