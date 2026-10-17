"""Versioned conservative paper vetoes and persistent operator-reset circuit breakers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, localcontext

import pandas as pd

from metatr.backtest import FuturesLedger, mark_ledger
from metatr.calendars import SessionCalendar, stamp
from metatr.contract_economics import CostScenario, decimal, number
from metatr.futures_core import Market
from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.research import canonical, digest


@dataclass(frozen=True)
class PaperRiskPolicy:
    account: str
    market: Market
    policy_id: str
    version: str
    initial_equity: str
    currency: str
    max_order_notional: str
    max_position_notional: str
    max_open_contracts: str
    max_gross_exposure: str
    max_net_exposure: str
    max_leverage: str
    max_concurrent_positions: int
    max_orders_per_bar: int
    cooldown_bars: int
    max_realized_daily_loss: str
    max_drawdown_fraction: str
    max_stale_bars: int
    margin_proxy: str
    assumptions: str

    @property
    def fingerprint(self):
        return digest(asdict(self))

    def validate(self):
        if (
            not all((self.policy_id, self.version, self.account, self.assumptions))
            or self.currency != "USD"
            or self.margin_proxy != "ASSUMED_FULLY_COLLATERALIZED"
            or any(
                decimal(getattr(self, key)) <= 0
                for key in (
                    "initial_equity",
                    "max_order_notional",
                    "max_position_notional",
                    "max_open_contracts",
                    "max_gross_exposure",
                    "max_net_exposure",
                    "max_leverage",
                    "max_realized_daily_loss",
                    "max_drawdown_fraction",
                )
            )
            or decimal(self.max_leverage) > 1
            or decimal(self.max_drawdown_fraction) > 1
            or any(
                type(getattr(self, key)) is not int
                for key in (
                    "max_concurrent_positions",
                    "max_orders_per_bar",
                    "cooldown_bars",
                    "max_stale_bars",
                )
            )
            or self.max_concurrent_positions != 1
            or self.max_orders_per_bar != 1
            or self.cooldown_bars < 0
            or not 1 <= self.max_stale_bars <= 100
        ):
            raise ValueError("Unsupported/invalid risk, currency or margin proxy policy")


@dataclass(frozen=True)
class PaperMarketState:
    dataset: object
    costs: CostScenario
    mode: str = "REPLAY"
    source_health: str = "OK"


@dataclass(frozen=True)
class RiskDecision:
    action: str
    reasons: tuple[str, ...]
    policy_id: str
    policy_version: str
    policy_sha256: str
    intent_id: str | None
    timestamp: str
    data_sha256: str
    ledger_sha256: str
    details_json: str
    signal_action: str = "NO_TRADE"
    execution: str = "PAPER_ONLY"

    def to_dict(self):
        result = asdict(self)
        result["details"] = json.loads(result.pop("details_json"))
        return result


def evaluate(
    intent,
    current_ledger,
    validated_latest_market: PaperMarketState,
    policy: PaperRiskPolicy,
    contract_spec,
    now_utc,
    *,
    phase="intent",
    allow_halted=False,
) -> RiskDecision:
    """Pure veto. No probability or model confidence can override these checks."""
    now = stamp(now_utc)
    state = current_ledger
    dataset = validated_latest_market.dataset
    reasons, details = [], {}
    action = "APPROVE"
    try:
        policy.validate()
        dataset.verify()
        report = json.loads(dataset.validation_json)
        if not report or not report["ready"] or report["data_sha256"] != dataset.data_sha256:
            raise ValueError("UNVERIFIED_DATASET")
        checksum = report.pop("report_sha256")
        if digest(report) != checksum:
            raise ValueError("UNVERIFIED_DATASET")
        if (
            policy.account != state["account"]
            or policy.market != dataset.market
            or state["market"] != dataset.market.key
            or contract_spec.market != dataset.market
            or state["currency"] != policy.currency
            or decimal(state["initial_equity"]) != decimal(policy.initial_equity)
        ):
            raise ValueError("MARKET_ACCOUNT_CURRENCY_MISMATCH")
        calendar = report["calendar"]
        if (
            not calendar
            or calendar["calendar_id"] != contract_spec.calendar_id
            or calendar["version"] != contract_spec.calendar_version
        ):
            raise ValueError("UNKNOWN_SESSION_CALENDAR")
        bars = dataset.bars
        seconds = TIMEFRAME_SECONDS[dataset.market.timeframe]
        latest = bars.index[-1] + pd.Timedelta(seconds=seconds)
        if (
            validated_latest_market.mode not in ("REPLAY", "PUBLIC")
            or validated_latest_market.source_health != "OK"
        ):
            raise ValueError("SOURCE_UNHEALTHY")
        if latest > now or (now - latest).total_seconds() > policy.max_stale_bars * seconds:
            raise ValueError("STALE_OR_OPEN_CANDLE")
        if ((bars.index.asi8[1:] - bars.index.asi8[:-1]) != seconds * 10**9).any():
            raise ValueError("UNSUPPORTED_SESSION_GAP")
        if validated_latest_market.mode == "PUBLIC":
            if stamp(dataset.asof_utc) > now or any(
                p["source"] != "coinbase_public_futures" for p in dataset.provenance
            ):
                raise ValueError("PUBLIC_PROVENANCE_OR_CLOCK_ERROR")
            calendar_body = dict(calendar)
            calendar_body["market"] = Market(**calendar_body["market"])
            current_open = now.floor(pd.Timedelta(seconds=seconds))
            if not len(
                SessionCalendar(**calendar_body).expected(
                    dataset.market, current_open, current_open + pd.Timedelta(seconds=seconds)
                )
            ):
                raise ValueError("CLOSED_SESSION")
        for item in dataset.provenance:
            contract_spec.validate(item["contract_metadata"], now)
        costs = validated_latest_market.costs
        costs.validate(contract_spec, now)
        if any(stamp(mark["timestamp"]) > now for mark in state["marks"]):
            raise ValueError("CLOCK_ROLLBACK")
        with localcontext() as context:
            context.prec = 40
            ledger = FuturesLedger(**state["ledger"])
            price = decimal(str(bars.iloc[-1].close))
            current = mark_ledger(ledger, number(price), contract_spec)
            equity = decimal(current["equity"])
            peak = max(
                [decimal(policy.initial_equity), equity]
                + [decimal(m["data"]["equity"]) for m in state["marks"]]
            )
            drawdown = (peak - equity) / peak
            daily_cash = sum(
                decimal(event["amount"])
                for event in state.get("cash_events", [])
                if event["reason"] != "SIMULATED_OPENING"
                and stamp(event["occurred_at"]).date() == now.date()
            )
            details.update(
                equity=number(equity),
                drawdown=number(drawdown),
                realized_daily_cash_pnl=number(daily_cash),
                margin_assumption=policy.margin_proxy,
            )
            if equity <= 0:
                reasons.append("NONPOSITIVE_EQUITY")
            if drawdown >= decimal(policy.max_drawdown_fraction):
                reasons.append("DRAWDOWN_LIMIT")
            if -daily_cash >= decimal(policy.max_realized_daily_loss):
                reasons.append("DAILY_LOSS_LIMIT")
            if reasons:
                action = "HALT"
            if state["halted"] and not allow_halted:
                action, reasons = "HALT", reasons + ["KILL_SWITCH_LATCHED"]
            if intent is not None and action == "APPROVE":
                order = next(
                    (o for o in state["orders"] if o["intent_id"] == intent["intent_id"]), None
                )
                allowed_status = (
                    "NEW" if phase == "intent" else "ACCEPTED" if phase == "fill" else None
                )
                if order is None or order["status"] != allowed_status:
                    reasons.append("DUPLICATE_OR_INVALID_ORDER_STATE")
                quantity = decimal(intent["quantity"])
                step = decimal(contract_spec.quantity_step)
                if (
                    quantity < decimal(contract_spec.min_quantity)
                    or quantity % step
                    or intent["side"] not in ("BUY", "SELL")
                    or intent["market"] != state["market"]
                ):
                    reasons.append("INVALID_QUANTITY_OR_INTENT")
                signed = quantity if intent["side"] == "BUY" else -quantity
                post = decimal(ledger.contracts) + signed
                tick = decimal(contract_spec.tick_size)
                execution_price = decimal(str(bars.iloc[-1].high)) if phase == "fill" else price
                worst = execution_price * (1 + decimal(costs.spread_bps) / 20000) + tick * decimal(
                    costs.slippage_ticks
                )
                worst = (worst / tick).to_integral_value(rounding=ROUND_CEILING) * tick
                notional = abs(post) * decimal(contract_spec.multiplier) * worst
                order_notional = quantity * decimal(contract_spec.multiplier) * worst
                fee = order_notional * decimal(costs.taker_fee_bps) / 10000
                buffered = notional + fee
                details.update(
                    post_contracts=number(post),
                    worst_price=number(worst),
                    post_notional=number(notional),
                    fee_buffer=number(fee),
                )
                if order_notional > decimal(policy.max_order_notional):
                    reasons.append("ORDER_NOTIONAL_LIMIT")
                if buffered > decimal(policy.max_position_notional):
                    reasons.append("POSITION_NOTIONAL_LIMIT")
                if abs(post) > decimal(policy.max_open_contracts):
                    reasons.append("CONTRACT_LIMIT")
                if buffered > decimal(policy.max_gross_exposure):
                    reasons.append("GROSS_EXPOSURE_LIMIT")
                if buffered > decimal(policy.max_net_exposure):
                    reasons.append("NET_EXPOSURE_LIMIT")
                if equity - fee <= 0 or buffered > (equity - fee) * decimal(policy.max_leverage):
                    reasons.append("LEVERAGE_OR_FEE_EQUITY_LIMIT")
                needed = (
                    quantity * decimal(contract_spec.multiplier)
                    if costs.volume_units == "base"
                    else quantity
                )
                if needed > decimal(str(bars.iloc[-1].volume)) * decimal(costs.participation_cap):
                    reasons.append("INSUFFICIENT_LIQUIDITY")
                closed = stamp(intent["closed_bar_utc"])
                if closed > latest or (phase == "intent" and closed != latest):
                    reasons.append("INTENT_TIME_MISMATCH")
                if phase == "intent":
                    accepted_ids = {
                        o["intent_id"]
                        for o in state["orders"]
                        if o["status"] in ("ACCEPTED", "FILLED", "CANCELLED", "EXPIRED")
                    }
                    accepted = [i for i in state["intents"] if i["intent_id"] in accepted_ids]
                    if (
                        sum(i["closed_bar_utc"] == intent["closed_bar_utc"] for i in accepted)
                        >= policy.max_orders_per_bar
                    ):
                        reasons.append("ORDERS_PER_BAR_LIMIT")
                    if accepted and policy.cooldown_bars:
                        prior = max(stamp(i["closed_bar_utc"]) for i in accepted)
                        if (closed - prior).total_seconds() < policy.cooldown_bars * seconds:
                            reasons.append("COOLDOWN")
                if reasons:
                    action = "REJECT"
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
        code = str(exc) if re.fullmatch(r"[A-Z_]+", str(exc)) else "INVALID_SPEC_OR_POLICY"
        action, reasons = "HALT", [code]
        details["error"] = str(exc)[:250]
    return RiskDecision(
        action,
        tuple(sorted(set(reasons))),
        policy.policy_id,
        policy.version,
        policy.fingerprint,
        intent["intent_id"] if intent else None,
        now.isoformat(),
        dataset.data_sha256,
        digest(state["ledger"]),
        canonical(details),
    )


class PaperRiskEngine:
    def __init__(self, store):
        self.store = store

    def pin_policy(self, policy, now_utc):
        policy.validate()
        state = self.store.inspect(policy.account)
        body = {"type": "POLICY", "policy": asdict(policy), "policy_sha256": policy.fingerprint}
        for item in state["risk_events"]:
            prior = item["data"]
            if (
                prior.get("type") == "POLICY"
                and prior["policy"]["policy_id"] == policy.policy_id
                and prior["policy"]["version"] == policy.version
            ):
                if prior != json.loads(canonical(body)):
                    raise ValueError("Immutable risk policy version conflict")
                return
        self.store.append(
            policy.account,
            "RISK_EVENT",
            ["POLICY", policy.policy_id, policy.version],
            body,
            now_utc,
        )

    def assess(
        self, account, intent_id, market_state, policy, contract, now_utc, *, phase="intent"
    ):
        if policy.account != account:
            raise ValueError("Risk policy/account mismatch")
        self.pin_policy(policy, now_utc)
        try:
            self.store.reconcile(account)
        except (ValueError, KeyError, TypeError) as exc:
            self.store.halt(account, "LEDGER_INCONSISTENT", at_utc=now_utc)
            raise ValueError("Paper reconciliation failed; account halted") from exc
        state = self.store.inspect(account)
        intent = next((i["data"] for i in state["intents"] if i["intent_id"] == intent_id), None)
        if intent is None:
            raise ValueError("Unknown paper intent")
        decision = evaluate(intent, state, market_state, policy, contract, now_utc, phase=phase)
        body = decision.to_dict()
        self.store.append(account, "RISK_EVENT", digest(body), body, now_utc)
        if decision.action == "HALT":
            self.store.halt(account, ";".join(decision.reasons), at_utc=now_utc)
        return decision

    def reset(self, account, policy, market_state, contract, now_utc, reason):
        if not reason.strip():
            raise ValueError("Explicit operator reset reason required")
        self.pin_policy(policy, now_utc)
        self.store.reconcile(account)
        state = self.store.inspect(account)
        if not state["halted"]:
            raise ValueError("Account is not halted")
        decision = evaluate(None, state, market_state, policy, contract, now_utc, allow_halted=True)
        if decision.action != "APPROVE":
            raise ValueError(f"Reset health check failed: {decision.reasons}")
        self.store.reset_halt(
            account, reason + " policy=" + policy.fingerprint, at_utc=now_utc, health_checked=True
        )
