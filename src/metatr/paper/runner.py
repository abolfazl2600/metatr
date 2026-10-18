"""Explicit opt-in paper orchestration with account locks and restart checkpoints."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, replace
from decimal import localcontext
from pathlib import Path

import pandas as pd

from metatr.calendars import stamp
from metatr.contract_economics import decimal, number
from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.paper.risk import PaperMarketState, evaluate
from metatr.paper.simulator import PaperExecutionSimulator
from metatr.research import canonical, code_revision, digest
from metatr.strategies import StrategyContext, predict
from metatr.validation import slice_dataset


class WorkerAlreadyRunning(ValueError):
    """An active account worker must retain its lock without a second worker interfering."""


def failure_code(exc):
    text = str(exc)
    if re.fullmatch(r"[A-Z][A-Z0-9_]*(?::[A-Z0-9_]+)?", text):
        return text
    lower = text.lower()
    for fragment, code in (
        ("disk full", "DISK_FULL"),
        ("locked", "SQLITE_LOCKED"),
        ("expired", "EXPIRED_CONTRACT"),
        ("retired", "MODEL_RETIRED"),
        ("contract", "CONTRACT_SPEC_INVALID"),
        ("reconciliation", "LEDGER_INCONSISTENT"),
    ):
        if fragment in lower:
            return code
    return type(exc).__name__.upper()


@contextmanager
def account_lock(path, account):
    """POSIX local-filesystem lock released automatically on process death."""
    try:
        import fcntl
    except ImportError as exc:
        raise ValueError("Paper worker requires POSIX file locking") from exc
    target = Path(path).with_name(Path(path).name + "." + digest(account)[:16] + ".lock")
    fd = os.open(target, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkerAlreadyRunning("Another paper worker holds this account lock") from exc
        yield
    finally:
        os.close(fd)


def checkpoint(state):
    rows = [r["data"] for r in state["risk_events"] if r["data"].get("type") == "CHECKPOINT"]
    return max((stamp(r["closed_bar_utc"]) for r in rows), default=None)


def status(store, account, now_utc):
    with localcontext() as context:
        context.prec = 40
        return _status(store, account, now_utc)


def _status(store, account, now_utc):
    state = store.inspect(account)
    ledger = state["ledger"]
    initial = decimal(state["initial_equity"])
    equity = decimal(state["marks"][-1]["data"]["equity"]) if state["marks"] else initial
    peak = max([initial, equity] + [decimal(m["data"]["equity"]) for m in state["marks"]])
    latest = checkpoint(state)
    pins = state["runs"][-1]["pins"] if state["runs"] else {}
    interval = pins.get("source_interval")
    return {
        "schema_version": 1,
        "label": "PAPER_ONLY_SIMULATED",
        "account": account,
        "market": state["market"],
        "initial_equity": number(initial),
        "current_equity": number(equity),
        "cash": ledger["cash"],
        "net_pnl": number(equity - initial),
        "realized_pnl": ledger["realized_pnl"],
        "unrealized_pnl": number(equity - decimal(ledger["cash"])),
        "fees": ledger["fees"],
        "assumed_funding": ledger["funding"],
        "open_contracts": ledger["contracts"],
        "average_entry": ledger["average_entry"],
        "exposure": state["marks"][-1]["data"]["notional"] if state["marks"] else "0",
        "drawdown": number((peak - equity) / peak),
        "halted": bool(state["halted"]),
        "last_committed_candle_close_utc": latest.isoformat() if latest else None,
        "source_received_utc": interval[2] if interval else None,
        "ingest_lag_seconds": max(0, (stamp(interval[2]) - stamp(interval[1])).total_seconds())
        if interval
        else None,
        "model_id": pins.get("strategy_id"),
        "model_version": pins.get("strategy_version"),
        "model_config_sha256": pins.get("strategy_config_sha256"),
        "contract_sha256": digest(pins["contract_spec"]) if pins else None,
        "reporting_currency": state["currency"],
        "stale_age_seconds": max(0, (stamp(now_utc) - latest).total_seconds()) if latest else None,
        "accepted_intents": sum(
            json.loads(o["approval_json"]).get("action") == "APPROVE" for o in state["orders"]
        ),
        "rejected_intents": sum(o["status"] == "REJECTED" for o in state["orders"]),
        "pending_orders": sum(o["status"] == "ACCEPTED" for o in state["orders"]),
        "closed_trades": sum(f["data"]["trade"] is not None for f in state["fills"]),
        "runs": state["runs"][-3:],
        "recent_risk_events": state["risk_events"][-10:],
        "recent_halt_events": state["halt_events"][-5:],
        "execution": "PAPER_ONLY",
        "real_execution_available": False,
    }


class PaperRunner:
    def __init__(self, store, account, *, audit_log=None):
        self.store, self.account = store, account
        self.simulator = PaperExecutionSimulator(store)
        self.audit_log = Path(audit_log) if audit_log else None

    def _log(self, code, correlation_id, **fields):
        if self.audit_log is None:
            return
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        # Only allowlisted structured fields, no request headers, credentials or raw responses.
        body = {
            "label": "PAPER_ONLY",
            "code": code,
            "correlation_id": correlation_id,
            "account": self.account,
            **fields,
        }
        fd = os.open(self.audit_log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(canonical(body) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def process(
        self,
        dataset,
        spec,
        contract,
        costs,
        policy,
        contracts,
        *,
        mode="REPLAY",
        now_utc=None,
        max_catch_up_bars=350,
        stage_hook=None,
    ):
        with account_lock(self.store.path, self.account):
            return self._process(
                dataset,
                spec,
                contract,
                costs,
                policy,
                contracts,
                mode=mode,
                now_utc=now_utc,
                max_catch_up_bars=max_catch_up_bars,
                stage_hook=stage_hook,
            )

    def _process(
        self,
        dataset,
        spec,
        contract,
        costs,
        policy,
        contracts,
        *,
        mode="REPLAY",
        now_utc=None,
        max_catch_up_bars=350,
        stage_hook=None,
    ):
        """Caller must hold account_lock for the entire public polling session."""
        failure_at = stamp(dataset.end_utc)
        try:
            failure_at = stamp(now_utc or dataset.end_utc)
            dataset.verify()
            self.store.reconcile(self.account)
            state = self.store.inspect(self.account)
            if mode not in ("REPLAY", "PUBLIC") or (mode == "PUBLIC" and now_utc is None):
                raise ValueError("Explicit valid paper clock/mode required")
            if spec.status == "retired":
                raise ValueError("MODEL_RETIRED")
            if (
                spec.market != dataset.market
                or policy.account != self.account
                or decimal(contracts) <= 0
                or type(max_catch_up_bars) is not int
                or not 1 <= max_catch_up_bars <= 10000
            ):
                raise ValueError("Invalid frozen strategy/quantity/account/catch-up policy")
            if any(r["pins"].get("mode", mode) != mode for r in state["runs"]):
                raise ValueError("Use a separate paper account for PUBLIC versus REPLAY")
            if state["halted"]:
                self.store.stop_pending(self.account, "PERSISTENT_HALT", at_utc=failure_at)
                return {"processed_bars": 0, **status(self.store, self.account, failure_at)}
            bars = dataset.bars
            seconds = TIMEFRAME_SECONDS[dataset.market.timeframe]
            last = checkpoint(state)
            if last is not None and last > stamp(dataset.end_utc):
                raise ValueError("DATA_OR_CLOCK_ROLLBACK")
            positions = [
                i
                for i, opened in enumerate(bars.index)
                if last is None or opened + pd.Timedelta(seconds=seconds) > last
            ]
            if mode == "PUBLIC" and last is None:
                positions = [
                    len(bars) - 1
                ]  # initial live startup never fabricates historical paper fills
            if positions and last is not None and bars.index[positions[0]] != last:
                raise ValueError("CATCHUP_GAP")
            if len(positions) > max_catch_up_bars:
                raise ValueError("CATCHUP_LIMIT")
            # Validate the newest public capture against the real clock before any
            # catch-up work. Historical prefixes are evaluated at their own bar-close
            # clock below; comparing each old prefix to wall-clock time falsely marks
            # a healthy, fresh capture as stale and halts every restarted worker.
            if mode == "PUBLIC":
                current_state = self.store.inspect(self.account)
                current_health = evaluate(
                    None, current_state, PaperMarketState(dataset, costs, mode),
                    policy, contract, failure_at,
                )
                if current_health.action != "APPROVE":
                    body = {"type": "HEALTH", "phase": "PUBLIC_CAPTURE_GATE",
                            **current_health.to_dict()}
                    self.store.append(
                        self.account, "RISK_EVENT", digest(body), body, failure_at
                    )
                    self.store.halt(
                        self.account, ";".join(current_health.reasons), at_utc=failure_at
                    )
                    self.store.stop_pending(
                        self.account, "HEALTH_HALT", at_utc=failure_at
                    )
                    self.store.reconcile(self.account)
                    return {
                        "processed_bars": 0,
                        **status(self.store, self.account, failure_at),
                    }
            pins = {
                "execution": "PAPER_ONLY",
                "mode": mode,
                "code_revision": code_revision(),
                "seed": 0,
                "event_policy": "IGNORE_SAME_DIRECTION_REVERSE_OPPOSITE",
                "market": dataset.market.key,
                "strategy_id": spec.strategy_id,
                "strategy_version": spec.version,
                "strategy_config_sha256": spec.config_sha256,
                "data_sha256": dataset.data_sha256,
                "dataset_report_sha256": json.loads(dataset.validation_json)["report_sha256"],
                "contract_spec": asdict(contract),
                "contract_metadata": dataset.provenance[0]["contract_metadata"],
                "cost_scenario": asdict(costs),
                "cost_sha256": costs.fingerprint,
                "risk_policy_sha256": policy.fingerprint,
                "snapshot_ids": dataset.snapshot_ids,
                "source_interval": [dataset.start_utc, dataset.end_utc, dataset.asof_utc],
            }
            at = (
                bars.index[-1] + pd.Timedelta(seconds=seconds)
                if mode == "PUBLIC"
                else stamp(dataset.start_utc)
            )
            run_id = self.store.register_run(self.account, pins, at_utc=at)
            self.simulator.risk.pin_policy(policy, failure_at)
            processed, latest_decision = 0, None
            for pos in positions:
                opened = bars.index[pos]
                closed = opened + pd.Timedelta(seconds=seconds)
                observed_now = stamp(now_utc) if mode == "PUBLIC" else closed
                failure_at = observed_now
                # Missed bars are simulated at their event-time clock only after the
                # full current public dataset has passed the real-clock health gate.
                # Recent bars still use PUBLIC checks against the actual clock.
                historical_catchup = (
                    mode == "PUBLIC"
                    and (observed_now - closed).total_seconds()
                    > policy.max_stale_bars * seconds
                )
                bar_now = closed if historical_catchup or mode != "PUBLIC" else observed_now
                bar_mode = "REPLAY" if historical_catchup else mode
                prefix = slice_dataset(dataset, 0, pos + 1)
                health = PaperMarketState(prefix, costs, bar_mode)
                correlation = digest(
                    {
                        "account": self.account,
                        "market": dataset.market.key,
                        "closed": closed.isoformat(),
                        "config": spec.config_sha256,
                    }
                )
                self._log(
                    "BAR_BEGIN",
                    correlation,
                    market=dataset.market.key,
                    market_close_utc=closed.isoformat(),
                    observed_utc=observed_now.isoformat(),
                )
                check = evaluate(
                    None, self.store.inspect(self.account), health, policy, contract, bar_now
                )
                check_body = {"type": "HEALTH", **check.to_dict()}
                self.store.append(
                    self.account, "RISK_EVENT", digest(check_body), check_body, bar_now
                )
                if check.action != "APPROVE":
                    self.store.halt(self.account, ";".join(check.reasons), at_utc=bar_now)
                    self.store.stop_pending(
                        self.account, "HEALTH_HALT", at_utc=bar_now
                    )
                    break
                for order in self.store.inspect(self.account)["orders"]:
                    if order["status"] == "ACCEPTED":
                        self.simulator.process_next_bar(
                            self.account, order["intent_id"], health, policy, contract, bar_now
                        )
                if stage_hook:
                    stage_hook("after_fills", closed)
                if self.store.inspect(self.account)["halted"]:
                    self.store.stop_pending(
                        self.account, "FILL_RISK_HALT", at_utc=bar_now
                    )
                    break
                self.store.funding(self.account, run_id, str(bars.iloc[pos].close), at_utc=closed)
                if stage_hook:
                    stage_hook("after_funding", closed)
                self.store.mark(self.account, run_id, str(bars.iloc[pos].close), at_utc=closed)
                if stage_hook:
                    stage_hook("after_mark", closed)
                context = StrategyContext(
                    dataset.market,
                    opened,
                    closed,
                    stamp(dataset.asof_utc),
                    prefix.snapshot_ids,
                    prefix.data_sha256,
                    spec.warmup_bars,
                    0,
                )
                decision = predict(prefix.bars, spec, context)
                signal_key = ["SIGNAL", closed.isoformat()]
                prior = self.store.event(self.account, "RISK_EVENT", signal_key)
                if prior:
                    old = prior["payload"]["decision"]
                    if any(
                        old[k] != decision.to_dict()[k]
                        for k in (
                            "action",
                            "strategy_id",
                            "strategy_version",
                            "config_sha256",
                            "timestamp",
                        )
                    ):
                        raise ValueError("CONFLICTING_RESUMED_SIGNAL")
                else:
                    self.store.append(
                        self.account,
                        "RISK_EVENT",
                        signal_key,
                        {"type": "SIGNAL", "run_id": run_id, "decision": decision.to_dict()},
                        closed,
                    )
                current = decimal(self.store.inspect(self.account)["ledger"]["contracts"])
                side = {"LONG": 1, "SHORT": -1, "NO_TRADE": 0}[decision.action]
                if side and current * side <= 0:
                    delta = decimal(contracts) * side - current
                    intent_id = self.store.new_intent(
                        self.account,
                        run_id,
                        closed,
                        "BUY" if delta > 0 else "SELL",
                        number(abs(delta)),
                        data_sha256=prefix.data_sha256,
                    )
                    accepted = self.simulator.approve(
                        self.account, intent_id, health, policy, contract, bar_now
                    )
                    if accepted != "ACCEPTED":
                        decision = replace(
                            decision, action="NO_TRADE", reason="PAPER_SIMULATED_RISK_VETO"
                        )
                if stage_hook:
                    stage_hook("after_intent", closed)
                self.store.append(
                    self.account,
                    "RISK_EVENT",
                    ["CHECKPOINT", closed.isoformat()],
                    {
                        "type": "CHECKPOINT",
                        "run_id": run_id,
                        "closed_bar_utc": closed.isoformat(),
                        "data_sha256": prefix.data_sha256,
                    },
                    closed,
                )
                processed += 1
                latest_decision = replace(decision, reason="PAPER_SIMULATED " + decision.reason)
                self._log("BAR_COMMITTED", correlation, market_close_utc=closed.isoformat())
                if stage_hook:
                    stage_hook("after_checkpoint", closed)
            self.store.reconcile(self.account)
            return {
                "processed_bars": processed,
                "last_decision": latest_decision,
                **status(self.store, self.account, failure_at),
            }
        except Exception as exc:
            try:
                self.store.halt(
                    self.account, "RUNNER_FAILURE:" + failure_code(exc), at_utc=failure_at
                )
                self.store.stop_pending(self.account, "RUNNER_FAILURE", at_utc=failure_at)
            except Exception:
                # Storage failure may prevent persisting HALT; stop the worker anyway.
                raise RuntimeError("PAPER_STOPPED_HALT_PERSIST_FAILED") from exc
            raise
