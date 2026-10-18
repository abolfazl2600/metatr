"""Prepare a public-data PAPER scenario and optionally start its existing runner."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd

from metatr.calendars import SessionCalendar, stamp
from metatr.contract_economics import (
    ContractSpec, CostScenario, decimal, native_contract_fingerprint,
)
from metatr.dataset_quality import validate_dataset
from metatr.exchanges.coinbase_futures import CoinbaseFuturesFeed
from metatr.futures_store import FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry, StrategySpec
from metatr.paper.risk import PaperMarketState, PaperRiskPolicy, evaluate
from metatr.paper.runner import WorkerAlreadyRunning, account_lock, checkpoint
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


def _capture_with_retry(feed, *, now=None, wait=time.sleep):
    """Retry transient public reads before creating any persistent Paper state.

    Deliberately do not retry 403/404/429: they require operator review and
    must never be interpreted as permission to fall back to another market.
    No private/authenticated endpoints are used.
    """
    for attempt in range(3):
        try:
            kwargs = {"now": now} if now is not None else {}
            return feed.capture_candles(MARKET.product_id, "M15", 80, **kwargs)
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



def _file_sha256(content):
    return hashlib.sha256(content).hexdigest()


def _read_regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing, non-regular or symlink configuration: {path.name}")
    return path.read_bytes()


def _active_config_path(workspace, profile, name):
    """Resolve immutable session-generation configs, with legacy root-file fallback."""
    generation = profile.get("active_session_generation")
    if not generation:
        path = workspace / f"{name}.json"
        _read_regular(path)
        return path
    relative = Path(generation)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Unsafe active session generation path")
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Refuse symlink in active session generation path")
    if not current.is_dir():
        raise ValueError("Active session generation is missing")
    path = current / f"{name}.json"
    raw = _read_regular(path)
    expected = profile.get(f"active_session_{name}_file_sha256")
    if not expected or _file_sha256(raw) != expected:
        raise ValueError(f"Active session {name} content hash mismatch")
    return path


def _load_contract(path):
    body = json.loads(_read_regular(path))
    body["market"] = parse_market(body["market"])
    return ContractSpec(**body)


def _load_calendar(path):
    body = json.loads(_read_regular(path))
    body["market"] = parse_market(body["market"])
    body["closures"] = tuple(tuple(item) for item in body.get("closures", []))
    return SessionCalendar(**body)


def _build_confirmed_calendar(product, captured_at):
    """Build a calendar solely from this exact public product's current session."""
    if any(product.get(key) for key in ("is_disabled", "trading_disabled", "cancel_only")):
        raise ValueError("PUBLIC_CONTRACT_UNAVAILABLE")
    session = product.get("fcm_trading_session_details")
    if not isinstance(session, dict) or (
        session.get("is_session_open") is not True
        or session.get("session_state") != "FCM_TRADING_SESSION_STATE_OPEN"
    ):
        raise ValueError("PUBLIC_SESSION_CLOSED")
    now = stamp(captured_at)
    opened, closed = stamp(session["open_time"]), stamp(session["close_time"])
    if not opened < closed or not opened <= now < closed:
        raise ValueError("PUBLIC_SESSION_TIME_UNCONFIRMED")
    step_ns = 900 * 10**9
    if opened.value % step_ns or closed.value % step_ns:
        raise ValueError("PUBLIC_SESSION_NOT_M15_ALIGNED")
    closures = []
    maintenance = session.get("maintenance")
    if maintenance:
        left, right = stamp(maintenance["start_time"]), stamp(maintenance["end_time"])
        if left >= right:
            raise ValueError("PUBLIC_MAINTENANCE_INVALID")
        if left < closed and right > opened:
            left, right = max(left, opened), min(right, closed)
            if left <= now < right:
                raise ValueError("PUBLIC_SESSION_MAINTENANCE")
            closures.append((left.isoformat(), right.isoformat()))
    version = digest({
        "market": MARKET.key,
        "open": opened.isoformat(),
        "close": closed.isoformat(),
        "closures": closures,
        "source": SOURCE,
    })[:16]
    calendar = SessionCalendar(
        MARKET, "coinbase-confirmed-session", version, SOURCE, "UTC",
        opened.isoformat(), closed.isoformat(), tuple(closures),
    )
    return calendar, opened, closed


def _durable_write(path, content, *, exclusive=False):
    path = Path(path)
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_immutable_json(path, value):
    content = (canonical(value) + "\n").encode("utf-8")
    _durable_write(path, content, exclusive=True)
    return _file_sha256(content)


def _checkpoint_continuity(state, dataset):
    """Renewal must not turn missing history into catch-up fills."""
    last = checkpoint(state)
    if last is None:
        if state["fills"]:
            raise ValueError("PAPER_FILL_WITHOUT_CHECKPOINT")
        return None
    last = stamp(last)
    start, end = stamp(dataset.start_utc), stamp(dataset.end_utc)
    if last < start or last > end:
        raise ValueError("UNOBSERVED_CHECKPOINT_GAP")
    if last != end and last not in dataset.bars.index:
        raise ValueError("UNOBSERVED_CHECKPOINT_GAP")
    if any(stamp(fill["data"]["timestamp"]) > last for fill in state["fills"]):
        raise ValueError("PAPER_FILL_AFTER_CHECKPOINT")
    return last


def renew_session(workspace, *, feed=None, now=None, before_commit_hook=None):
    """Renew only sourced session configuration; never mutates the Paper account."""
    workspace_arg = Path(workspace).expanduser()
    if workspace_arg.is_symlink() or not workspace_arg.is_dir():
        raise ValueError("Existing, non-symlink Paper workspace required")
    workspace = workspace_arg.resolve()
    setup_path = workspace / "setup.json"
    original_setup = _read_regular(setup_path)
    profile = json.loads(original_setup)
    if (profile.get("schema_version") != 1
            or profile.get("label") != "PAPER_ONLY_SIMULATED"
            or profile.get("account") != "bip-paper"
            or profile.get("market") != MARKET.key):
        raise ValueError("Unknown Paper setup profile")
    required = ("paper.sqlite", "market.sqlite", "registry.sqlite", "costs.json", "risk.json")
    for name in required:
        _read_regular(workspace / name)
    current_time = stamp(now or datetime.now(timezone.utc).isoformat())
    feed = feed or CoinbaseFuturesFeed()

    # The worker uses the same account lock. Renewal cannot race a live polling cycle.
    with account_lock(workspace / "paper.sqlite", profile["account"]):
        paper = PaperStore(workspace / "paper.sqlite")
        paper.reconcile(profile["account"])
        state = paper.inspect(profile["account"])
        if state["market"] != MARKET.key or state["account"] != profile["account"]:
            raise ValueError("PAPER_ACCOUNT_MARKET_MISMATCH")

        old_contract_path = _active_config_path(workspace, profile, "contract")
        old_calendar_path = _active_config_path(workspace, profile, "calendar")
        old_contract_raw = _read_regular(old_contract_path)
        old_calendar_raw = _read_regular(old_calendar_path)
        old_contract = _load_contract(old_contract_path)
        old_calendar = _load_calendar(old_calendar_path)
        if old_contract.fingerprint != profile.get("contract_sha256"):
            raise ValueError("PAPER_CONTRACT_PROFILE_PIN_MISMATCH")
        if (old_contract.calendar_id != old_calendar.calendar_id
                or old_contract.calendar_version != old_calendar.version
                or old_calendar.market != MARKET):
            raise ValueError("PAPER_CALENDAR_CONTRACT_PIN_MISMATCH")

        costs = CostScenario(**json.loads(_read_regular(workspace / "costs.json")))
        risk_body = json.loads(_read_regular(workspace / "risk.json"))
        risk_body["market"] = parse_market(risk_body["market"])
        policy = PaperRiskPolicy(**risk_body)
        policy.validate()
        if (costs.fingerprint != profile.get("cost_sha256")
                or policy.fingerprint != profile.get("risk_sha256")
                or policy.account != profile["account"] or policy.market != MARKET
                or state["currency"] != policy.currency
                or state["initial_equity"] != policy.initial_equity):
            raise ValueError("PAPER_COST_RISK_OR_ACCOUNT_PIN_MISMATCH")

        registry = StrategyRegistry(workspace / "registry.sqlite")
        spec = registry.resolve(MARKET, "momentum", "1.0.0")
        if spec.status == "retired":
            raise ValueError("MODEL_RETIRED")
        if state["runs"]:
            pins = state["runs"][-1]["pins"]
            if (pins.get("strategy_id") != spec.strategy_id
                    or pins.get("strategy_version") != spec.version
                    or pins.get("strategy_config_sha256") != spec.config_sha256
                    or pins.get("risk_policy_sha256") != policy.fingerprint):
                raise ValueError("PAPER_MODEL_OR_RISK_PIN_MISMATCH")

        statuses = {order["status"] for order in state["orders"]}
        if not statuses <= {"NEW", "ACCEPTED", "REJECTED", "CANCELLED", "EXPIRED"}:
            raise ValueError("PAPER_ORDER_STATE_UNKNOWN")
        if any(order["status"] in ("NEW", "ACCEPTED") for order in state["orders"]):
            raise ValueError("PAPER_PENDING_ORDER_REQUIRES_OPERATOR_REVIEW")
        if abs(decimal(state["ledger"]["contracts"])) > decimal(policy.max_open_contracts):
            raise ValueError("PAPER_POSITION_EXCEEDS_RISK_POLICY")

        capture = _capture_with_retry(feed, now=current_time.to_pydatetime())
        captured_at = stamp(capture.captured_at)
        end = capture.bars.index[-1] + pd.Timedelta(minutes=15)
        if len(capture.bars) != 80 or not end <= captured_at < end + pd.Timedelta(minutes=30):
            raise ValueError("PUBLIC_CAPTURE_STALE_OR_INCOMPLETE")
        calendar, opened, closed = _build_confirmed_calendar(capture.product, captured_at)
        first = capture.bars.index[0]
        if first < opened or end > closed:
            raise ValueError("PUBLIC_CAPTURE_OUTSIDE_CONFIRMED_SESSION")
        if calendar.version == old_calendar.version:
            raise ValueError("PUBLIC_SESSION_UNCHANGED")

        # Structural contract identity must stay the same; only the sourced calendar pin changes.
        old_contract.validate(capture.product, captured_at)
        contract = replace(old_contract, calendar_version=calendar.version)
        contract.validate(capture.product, captured_at)
        costs.validate(contract, captured_at)

        market_store = FuturesSQLiteStore(workspace / "market.sqlite")
        market_store.save_snapshot(
            MARKET, source="coinbase_public_futures",
            raw_payload=capture.raw_payload, contract_metadata=capture.product,
            bars=capture.bars, asof=captured_at.to_pydatetime(),
        )
        quality, dataset = validate_dataset(
            market_store, MARKET, first, end, captured_at, calendar=calendar,
            require_fresh=True,
        )
        if not quality.ready:
            raise ValueError(f"PUBLIC_DATA_NOT_READY:{quality.issues}")
        last_checkpoint = _checkpoint_continuity(state, dataset)

        # A pure health gate checks fresh public bars and existing account risk without
        # clearing a persisted HALT. Pending intents/fills are never edited here.
        health = evaluate(
            None, state, PaperMarketState(dataset, costs, "PUBLIC"),
            policy, contract, captured_at, allow_halted=True,
        )
        if health.action != "APPROVE":
            raise ValueError("PAPER_HEALTH_GATE_FAILED:" + ",".join(health.reasons))

        from metatr.paper_observe import observe

        if now is None:
            observation_path, observation = observe(workspace)
        else:
            observation_path, observation = observe(workspace, now_utc=current_time.isoformat())
        if not observation.get("reconciled"):
            raise ValueError("PAPER_OBSERVATION_RECONCILIATION_FAILED")
        observation_raw = _read_regular(observation_path)
        observation_body = json.loads(observation_raw)
        observation_id = observation_body.get("run_id")
        check_body = dict(observation_body)
        check_body.pop("run_id", None)
        if (observation_id != observation_path.stem
                or observation_id != digest(check_body)
                or observation_body.get("account") != profile["account"]
                or observation_body.get("market") != MARKET.key):
            raise ValueError("PAPER_OBSERVATION_EVIDENCE_INVALID")

        old_setup_sha = _file_sha256(original_setup)
        old_contract_sha = _file_sha256(old_contract_raw)
        old_calendar_sha = _file_sha256(old_calendar_raw)
        new_contract_raw = (canonical(_body(contract)) + "\n").encode("utf-8")
        new_calendar_raw = (canonical(_body(calendar)) + "\n").encode("utf-8")
        generation_seed = {
            "previous_setup_sha256": old_setup_sha,
            "previous_contract_sha256": old_contract_sha,
            "previous_calendar_sha256": old_calendar_sha,
            "calendar_version": calendar.version,
            "data_sha256": dataset.data_sha256,
            "source_received_utc": captured_at.isoformat(),
            "observation_sha256": _file_sha256(observation_raw),
        }
        generation_id = digest(generation_seed)
        generations = workspace / "session-generations"
        if generations.is_symlink():
            raise ValueError("Refuse symlink session-generations directory")
        generations.mkdir(mode=0o700, exist_ok=True)
        final_dir = generations / generation_id
        if final_dir.exists() or final_dir.is_symlink():
            raise ValueError("Session generation already exists; inspect prior renewal evidence")
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=generations))
        try:
            os.chmod(stage, 0o700)
            before = stage / "before"
            before.mkdir(mode=0o700)
            before_hashes = {}
            for name, raw in (
                ("setup", original_setup),
                ("contract", old_contract_raw),
                ("calendar", old_calendar_raw),
            ):
                sha = _file_sha256(raw)
                target = before / f"{name}-{sha}.json"
                _durable_write(target, raw, exclusive=True)
                before_hashes[name] = {"sha256": sha, "path": f"before/{target.name}"}
            contract_sha = _file_sha256(new_contract_raw)
            calendar_sha = _file_sha256(new_calendar_raw)
            _durable_write(stage / "contract.json", new_contract_raw, exclusive=True)
            _durable_write(stage / "calendar.json", new_calendar_raw, exclusive=True)
            metadata_raw = (canonical(capture.product) + "\n").encode("utf-8")
            _durable_write(stage / "public-metadata.json", metadata_raw, exclusive=True)
            event = {
                "schema_version": 1,
                "kind": "PAPER_SESSION_RENEWAL",
                "commit_rule": "COMMITTED_ONLY_WHEN_SETUP_POINTER_REFERENCES_THIS_GENERATION",
                "generation_id": generation_id,
                "account": profile["account"],
                "market": MARKET.key,
                "renewed_at_utc": current_time.isoformat(),
                "source_received_utc": captured_at.isoformat(),
                "source": SOURCE,
                "source_product_id": MARKET.product_id,
                "session_open_utc": opened.isoformat(),
                "session_close_utc": closed.isoformat(),
                "calendar_version": calendar.version,
                "previous_calendar_version": old_calendar.version,
                "contract_fingerprint": contract.fingerprint,
                "contract_native_metadata_sha256": contract.metadata_sha256,
                "previous_profile_sha256": old_setup_sha,
                "before_files": before_hashes,
                "after_files": {
                    "contract_sha256": contract_sha,
                    "calendar_sha256": calendar_sha,
                    "metadata_sha256": _file_sha256(metadata_raw),
                },
                "data_sha256": dataset.data_sha256,
                "dataset_report_sha256": json.loads(dataset.validation_json)["report_sha256"],
                "snapshot_ids": dataset.snapshot_ids,
                "last_committed_checkpoint_utc": (
                    last_checkpoint.isoformat() if last_checkpoint is not None else None
                ),
                "paper_halted_before_renewal": bool(state["halted"]),
                "active_pending_orders": 0,
                "paper_health_gate": health.action,
                "paper_health_reasons": list(health.reasons),
                "observation_evidence": {
                    "path": str(observation_path.relative_to(workspace)),
                    "run_id": observation_id,
                    "sha256": _file_sha256(observation_raw),
                    "observed_utc": observation_body["observed_utc"],
                    "health": observation_body["health"],
                    "alerts": observation_body["alerts"],
                    "clock_source": observation_body["clock_source"],
                },
                "paper_account_mutated": False,
                "halt_reset": False,
                "real_execution_available": False,
            }
            event_sha = _write_immutable_json(stage / "renewal.json", event)
            _fsync_directory(before)
            _fsync_directory(stage)
            os.rename(stage, final_dir)
            _fsync_directory(generations)
            if before_commit_hook is not None:
                before_commit_hook(final_dir)

            # setup.json is the single atomic commit pointer. The generation contains
            # both matching contract/calendar files and all old-file evidence already.
            if _read_regular(setup_path) != original_setup:
                raise ValueError("PAPER_SETUP_CHANGED_DURING_RENEWAL")
            updated = dict(profile)
            updated.update({
                "active_session_generation": f"session-generations/{generation_id}",
                "active_session_contract_file_sha256": contract_sha,
                "active_session_calendar_file_sha256": calendar_sha,
                "session_renewal_event_sha256": event_sha,
                "session_renewed_utc": current_time.isoformat(),
                "session_coverage_start_utc": opened.isoformat(),
                "session_coverage_end_utc": closed.isoformat(),
                "contract_sha256": contract.fingerprint,
                "calendar_sha256": calendar_sha,
                "last_session_renewal_id": generation_id,
            })
            setup_temp = workspace / f".setup-renewal-{generation_id}.tmp"
            _durable_write(
                setup_temp, (canonical(updated) + "\n").encode("utf-8"), exclusive=True,
            )
            os.replace(setup_temp, setup_path)
            _fsync_directory(workspace)
            return {
                "status": "SESSION_RENEWED_PAPER_ONLY",
                "generation_id": generation_id,
                "session_coverage_start_utc": opened.isoformat(),
                "session_coverage_end_utc": closed.isoformat(),
                "calendar_version": calendar.version,
                "paper_halted": bool(state["halted"]),
                "halt_reset": False,
                "paper_account_mutated": False,
                "real_execution_available": False,
                "renewal_event": str(final_dir / "renewal.json"),
            }
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
            config_path = (
                _active_config_path(workspace, profile, name)
                if name in ("contract", "calendar")
                else workspace / f"{name}.json"
            )
            _read_regular(config_path)
            args += [f"--{flag}", str(config_path)]
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
    mode.add_argument("--renew-session", action="store_true",
                      help="Renew only the explicitly confirmed public Coinbase session")
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
        if args.renew_session:
            if args.confirm_paper or args.max_cycles is not None or args.display_output_dir:
                raise ValueError("--renew-session cannot be combined with start-only options")
            print(canonical(renew_session(args.workspace)), flush=True)
            return 0
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
