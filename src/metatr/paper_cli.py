"""Explicit PAPER ONLY operator CLI. No real-money adapter or credentials exist."""

from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd

from metatr.calendars import SessionCalendar
from metatr.contract_economics import ContractSpec, CostScenario
from metatr.dataset_quality import validate_dataset
from metatr.exchanges.coinbase_futures import CoinbaseFuturesFeed
from metatr.futures_store import TIMEFRAME_SECONDS, FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry
from metatr.paper.risk import PaperMarketState, PaperRiskEngine, PaperRiskPolicy
from metatr.paper.runner import (
    PaperRunner,
    WorkerAlreadyRunning,
    account_lock,
    failure_code,
    status,
)
from metatr.paper.store import PaperStore
from metatr.paper_display import publish_paper_display
from metatr.paper_observe import observe
from metatr.research import canonical, digest, parse_market, write_experiment


def now_utc():
    return datetime.now(timezone.utc)


def _load(path):
    return json.loads(Path(path).read_text())


def configurations(args, market):
    for path in (args.store, args.registry):
        if not Path(path).is_file():
            raise ValueError("Existing market store and registry required")
    registry = StrategyRegistry(args.registry)
    spec = registry.resolve(market, args.strategy_id, args.strategy_version)
    if spec.status == "retired":
        raise ValueError("Retired strategy; paper operation blocked")
    contract = _load(args.contract_spec)
    contract["market"] = parse_market(contract["market"])
    costs = CostScenario(**_load(args.cost_scenario))
    calendar = _load(args.calendar)
    calendar["market"] = parse_market(calendar["market"])
    calendar["closures"] = tuple(tuple(w) for w in calendar.get("closures", []))
    policy = _load(args.risk_policy)
    policy["market"] = parse_market(policy["market"])
    policy = PaperRiskPolicy(**policy)
    if policy.account != args.account or policy.market != market:
        raise ValueError("Risk policy account/market mismatch")
    return registry, spec, ContractSpec(**contract), costs, SessionCalendar(**calendar), policy


def ready_dataset(args, market, calendar):
    if any(getattr(args, field, None) is None for field in ("start_utc", "end_utc", "asof_utc")):
        raise ValueError("Explicit start/end/asof UTC required for offline replay/health")
    report, dataset = validate_dataset(
        FuturesSQLiteStore(args.store),
        market,
        args.start_utc,
        args.end_utc,
        args.asof_utc,
        calendar=calendar,
    )
    if not report.ready:
        raise ValueError(f"Dataset not ready: {report.issues}")
    return dataset


def capture_public(feed, market, store, calendar, *, count=80, now=None, wait=time.sleep,
                   contract=None):
    """Bounded public-read retry only; never calls private or authenticated endpoints."""
    for attempt in range(3):
        try:
            capture = feed.capture_candles(market.product_id, market.timeframe, count, now=now)
            break
        except HTTPError as exc:
            if exc.code in (401, 403, 404) or attempt == 2:
                raise ValueError(f"PUBLIC_HTTP_{exc.code}") from None
            delay = 2**attempt
            if exc.code == 429:
                try:
                    delay = max(delay, int((exc.headers or {}).get("Retry-After", delay)))
                except (ValueError, TypeError):
                    raise ValueError("PUBLIC_429_UNVERIFIED_RETRY_WINDOW") from None
                if delay > 60:
                    raise ValueError("PUBLIC_429_LONG_COOLDOWN") from None
            elif exc.code < 500:
                raise ValueError(f"PUBLIC_HTTP_{exc.code}") from None
            wait(delay)
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise ValueError("PUBLIC_SOURCE_TIMEOUT") from None
            wait(2**attempt)
    if contract is not None:
        contract.validate(capture.product, capture.captured_at)
        product = capture.product
        if any(product.get(k) for k in ("is_disabled", "trading_disabled", "cancel_only")):
            raise ValueError("PUBLIC_CONTRACT_UNAVAILABLE")
        session = product.get("fcm_trading_session_details")
        if session is not None:
            observed = pd.Timestamp(capture.captured_at)
            if (not session.get("is_session_open")
                    or session.get("session_state") != "FCM_TRADING_SESSION_STATE_OPEN"
                    or not pd.Timestamp(session["open_time"]) <= observed < (
                        pd.Timestamp(session["close_time"]))):
                raise ValueError("PUBLIC_SESSION_CLOSED")
            maintenance = session.get("maintenance")
            if maintenance and pd.Timestamp(maintenance["start_time"]) <= observed < (
                pd.Timestamp(maintenance["end_time"])):
                raise ValueError("PUBLIC_SESSION_MAINTENANCE")
    store.save_snapshot(
        market,
        source="coinbase_public_futures",
        raw_payload=capture.raw_payload,
        contract_metadata=capture.product,
        bars=capture.bars,
        asof=capture.captured_at,
    )
    start = capture.bars.index[0]
    end = capture.bars.index[-1] + pd.Timedelta(seconds=TIMEFRAME_SECONDS[market.timeframe])
    report, dataset = validate_dataset(
        store, market, start, end, capture.captured_at, calendar=calendar, require_fresh=True
    )
    if not report.ready:
        raise ValueError(f"PUBLIC_DATA_NOT_READY:{report.issues}")
    return dataset


def _publish_paper(args, paper, dataset, decision, spec, contract, result, now):
    if not args.display_output_dir:
        result["display"] = {"status": "NOT_CONFIGURED"}
    elif result["halted"]:
        result["display"] = {"status": "PAPER_HALTED_NO_NEW_MARKERS"}
    elif decision is None:
        result["display"] = {"status": "NO_NEW_COMMITTED_BAR"}
    else:
        result["display"] = publish_paper_display(
            dataset, decision, spec, contract, result,
            args.display_output_dir, now_utc=now,
            evidence_dir=paper.path.parent / "display-evidence",
        )


def _verify_observation_workspace(args):
    if args.observation_workspace is None:
        return
    workspace = args.observation_workspace.expanduser().resolve()
    profile = json.loads((workspace / "setup.json").read_text())
    if (profile.get("schema_version") != 1
            or profile.get("label") != "PAPER_ONLY_SIMULATED"
            or profile.get("account") != args.account
            or profile.get("market") != args.market
            or (workspace / "paper.sqlite").resolve() != args.paper_db.resolve()):
        raise ValueError("Observation profile/account/paper DB mismatch")


def _record_observation(args, result, *, first=False):
    if args.observation_workspace is None:
        return
    if first or result["processed_bars"] or result["halted"]:
        path, record = observe(args.observation_workspace)
        result["observation"] = {
            "path": str(path), "health": record["health"],
            "alerts": record["alerts"], "reconciled": record["reconciled"],
        }
        if not record["reconciled"]:
            raise ValueError("PAPER_OBSERVATION_RECONCILIATION_FAILED")


def _data_arguments(parser):
    for name in (
        "store",
        "registry",
        "strategy-id",
        "strategy-version",
        "risk-policy",
        "contract-spec",
        "cost-scenario",
        "calendar",
    ):
        parser.add_argument(f"--{name}", required=True)
    for name in ("start-utc", "end-utc", "asof-utc"):
        parser.add_argument(f"--{name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="PAPER ONLY simulated account operations")
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {}
    for name in ("init", "run", "replay", "status", "halt", "reset-halt", "report"):
        command = sub.add_parser(name)
        commands[name] = command
        command.add_argument("--account", required=True)
        command.add_argument("--market", required=True, help="Complete five-part Market")
        command.add_argument("--paper-db", type=Path, required=True)
        command.add_argument("--now-utc")
    commands["init"].add_argument("--initial-equity", required=True)
    for name in ("halt", "reset-halt"):
        commands[name].add_argument("--reason", required=True)
    for name in ("run", "replay", "reset-halt"):
        _data_arguments(commands[name])
    for name in ("run", "replay"):
        command = commands[name]
        command.add_argument("--mode", choices=("paper",), required=True)
        command.add_argument("--confirm-paper", action="store_true")
        command.add_argument("--contracts", required=True)
        command.add_argument("--max-catch-up-bars", type=int, default=350)
        command.add_argument("--audit-log", type=Path)
        command.add_argument("--display-output-dir", type=Path)
    commands["run"].add_argument("--poll-seconds", type=int, default=30)
    commands["run"].add_argument("--count", type=int, default=80)
    commands["run"].add_argument("--max-cycles", type=int)
    commands["run"].add_argument("--observation-workspace", type=Path,
                                 help="Exact paper_setup workspace for automatic evidence")
    commands["reset-halt"].add_argument("--clock-mode", choices=("REPLAY", "PUBLIC"), required=True)
    commands["report"].add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    paper = None
    runner = None
    try:
        if args.command in ("run", "replay") and not args.confirm_paper:
            raise ValueError("PAPER ONLY requires explicit --mode paper --confirm-paper")
        market = parse_market(args.market)
        clock = args.now_utc or now_utc().isoformat()
        if args.command != "init" and not args.paper_db.is_file():
            raise ValueError("Initialize the paper account explicitly first")
        paper = PaperStore(args.paper_db)
        if args.command == "init":
            paper.initialize(args.account, market, args.initial_equity, at_utc=clock)
            print(canonical(status(paper, args.account, clock)))
            return 0
        if paper.inspect(args.account)["market"] != market.key:
            raise ValueError("Paper account market mismatch")
        if args.command in ("status", "report"):
            paper.reconcile(args.account)
            result = status(paper, args.account, clock)
            if args.command == "report":
                with paper.connection() as db:
                    result["audit_journal"] = [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM audit_events WHERE account=? ORDER BY sequence",
                            (args.account,),
                        )
                    ]
                result["ledger_details"] = paper.inspect(args.account)
                result = {"run_id": digest(result), **result}
                if args.output:
                    print(write_experiment(result, args.output))
                    return 0
            print(canonical(result))
            return 0
        if args.command == "halt":
            # HALT may be latched while the active worker holds its account lock.
            paper.halt(args.account, args.reason, at_utc=clock)
            paper.stop_pending(args.account, "MANUAL_HALT", at_utc=clock)
            print("PAPER ONLY HALTED")
            return 0
        registry, spec, contract, costs, calendar, policy = configurations(args, market)
        if args.command == "reset-halt":
            if args.clock_mode == "REPLAY" and not args.now_utc:
                raise ValueError("Historical reset requires explicit --now-utc replay clock")
            with account_lock(paper.path, args.account):
                dataset = ready_dataset(args, market, calendar)
                PaperRiskEngine(paper).reset(
                    args.account,
                    policy,
                    PaperMarketState(dataset, costs, args.clock_mode),
                    contract,
                    clock,
                    args.reason,
                )
            print("PAPER ONLY halt reset after fresh health checks")
            return 0
        if args.command == "run":
            _verify_observation_workspace(args)
        runner = PaperRunner(
            paper,
            args.account,
            audit_log=args.audit_log or paper.path.with_name(args.account + ".jsonl"),
        )
        if args.command == "replay":
            dataset = ready_dataset(args, market, calendar)
            result = runner.process(
                dataset,
                spec,
                contract,
                costs,
                policy,
                args.contracts,
                max_catch_up_bars=args.max_catch_up_bars,
            )
            decision = result.pop("last_decision", None)
            _publish_paper(args, paper, dataset, decision, spec, contract, result,
                           dataset.asof_utc)
            print(canonical(result))
            return 1 if result["halted"] else 0
        if (
            not 5 <= args.poll_seconds <= 60
            or not 52 <= args.count <= 350
            or (args.max_cycles is not None and args.max_cycles < 1)
            or args.now_utc
        ):
            raise ValueError("Public loop needs a real clock and bounded polling/count settings")
        stop = threading.Event()
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            for signum in previous:
                signal.signal(signum, lambda *_: stop.set())
            with account_lock(paper.path, args.account):
                cycles = 0
                while not stop.is_set():
                    spec_now = registry.resolve(market, args.strategy_id, args.strategy_version)
                    if spec_now.status == "retired" or spec_now.config_sha256 != spec.config_sha256:
                        raise ValueError("Pinned model retired or changed")
                    dataset = capture_public(
                        CoinbaseFuturesFeed(),
                        market,
                        FuturesSQLiteStore(args.store),
                        calendar,
                        count=args.count,
                        now=now_utc(),
                        contract=contract,
                    )
                    result = runner._process(
                        dataset,
                        spec_now,
                        contract,
                        costs,
                        policy,
                        args.contracts,
                        mode="PUBLIC",
                        now_utc=now_utc(),
                        max_catch_up_bars=args.max_catch_up_bars,
                    )
                    decision = result.pop("last_decision", None)
                    _publish_paper(args, paper, dataset, decision, spec_now, contract, result,
                                   now_utc())
                    _record_observation(args, result, first=cycles == 0)
                    print(canonical(result), flush=True)
                    if result["halted"]:
                        return 1
                    cycles += 1
                    if args.max_cycles and cycles >= args.max_cycles:
                        break
                    stop.wait(args.poll_seconds)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        return 0
    except WorkerAlreadyRunning as exc:
        print(f"PAPER ONLY worker rejected: {exc}", file=sys.stderr)
        return 1
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        sqlite3.Error,
        RuntimeError,
        ArithmeticError,
    ) as exc:
        persisted = False
        if paper is not None and args.command in ("run", "replay"):
            try:
                paper.halt(
                    args.account,
                    "OPERATIONAL_FAILURE:" + failure_code(exc),
                    at_utc=args.now_utc or now_utc(),
                )
                persisted = True
                paper.stop_pending(
                    args.account, "OPERATIONAL_FAILURE", at_utc=args.now_utc or now_utc()
                )
            except Exception:
                pass
        if runner is not None and getattr(args, "observation_workspace", None):
            try:
                observe(args.observation_workspace)
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
                pass
        if runner is not None:
            try:
                runner._log(
                    "OPERATIONAL_FAILURE",
                    digest([args.account, str(clock)]),
                    error_type=type(exc).__name__,
                    halt_persisted=persisted,
                )
            except OSError:
                pass
        print(
            canonical(
                {
                    "label": "PAPER_ONLY_STOPPED",
                    "halt_persisted": persisted,
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
