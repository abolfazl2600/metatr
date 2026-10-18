"""Risk-approved next-closed-bar paper fills, never a broker adapter."""

import json

import pandas as pd

from metatr.calendars import stamp
from metatr.contract_economics import SimulatedOrder, estimate_fill
from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.paper.risk import PaperRiskEngine
from metatr.research import digest


class PaperExecutionSimulator:
    def __init__(self, store):
        self.store = store
        self.risk = PaperRiskEngine(store)

    def approve(self, account, intent_id, market_state, policy, contract, now_utc):
        state = self.store.inspect(account)
        order = next((o for o in state["orders"] if o["intent_id"] == intent_id), None)
        if order is None:
            raise ValueError("Unknown paper intent")
        if order["status"] != "NEW":
            return order["status"]
        decision = self.risk.assess(account, intent_id, market_state, policy, contract, now_utc)
        status = "ACCEPTED" if decision.action == "APPROVE" else "REJECTED"
        self.store.transition(
            account,
            intent_id,
            status,
            at_utc=now_utc,
            reason=";".join(decision.reasons) or "RISK_APPROVED",
            approval=decision.to_dict(),
        )
        return status

    def process_next_bar(self, account, intent_id, market_state, policy, contract, now_utc):
        """A fill commits once, after the entire next eligible bar has closed.

        No partial fills. A missing/late next bar expires the order and latches halt.
        """
        try:
            self.store.reconcile(account)
        except (ValueError, KeyError, TypeError) as exc:
            self.store.halt(account, "LEDGER_INCONSISTENT", at_utc=now_utc)
            raise ValueError("Paper reconciliation failed") from exc
        state = self.store.inspect(account)
        order = next((o for o in state["orders"] if o["intent_id"] == intent_id), None)
        if order is None:
            raise ValueError("Unknown paper order")
        if order["status"] == "FILLED":
            return next(f["data"] for f in state["fills"] if f["intent_id"] == intent_id)
        if order["status"] != "ACCEPTED":
            return None
        intent = next(i["data"] for i in state["intents"] if i["intent_id"] == intent_id)
        expires_at = intent.get("expires_at_utc")
        if expires_at is not None and stamp(now_utc) >= stamp(expires_at):
            self.store.transition(
                account, intent_id, "EXPIRED", at_utc=now_utc, reason="INTENT_EXPIRED"
            )
            return None
        pins = next(r["pins"] for r in state["runs"] if r["run_id"] == intent["run_id"])
        approval = json.loads(order["approval_json"])
        if (
            approval.get("policy_sha256") != policy.fingerprint
            or digest(pins["contract_spec"]) != contract.fingerprint
            or pins["cost_sha256"] != market_state.costs.fingerprint
        ):
            self.store.transition(
                account,
                intent_id,
                "EXPIRED",
                at_utc=now_utc,
                reason="APPROVED_POLICY_OR_ECONOMICS_CHANGED",
            )
            return None
        bars = market_state.dataset.bars
        opened = bars.index[-1]
        seconds = TIMEFRAME_SECONDS[contract.market.timeframe]
        earliest = stamp(intent["closed_bar_utc"])
        now = stamp(now_utc)
        if opened < earliest:
            if now > earliest + pd.Timedelta(seconds=2 * seconds):
                self.store.transition(
                    account, intent_id, "EXPIRED", at_utc=now, reason="PENDING_BAR_TIMEOUT"
                )
                self.store.halt(account, "PENDING_BAR_TIMEOUT", at_utc=now)
            return None
        if opened > earliest:
            self.store.transition(
                account, intent_id, "EXPIRED", at_utc=now, reason="MISSING_NEXT_ELIGIBLE_BAR"
            )
            self.store.halt(account, "MISSING_NEXT_ELIGIBLE_BAR", at_utc=now)
            return None
        if now < opened + pd.Timedelta(seconds=seconds):
            return None
        recheck = self.risk.assess(
            account, intent_id, market_state, policy, contract, now, phase="fill"
        )
        if recheck.action != "APPROVE":
            self.store.transition(
                account,
                intent_id,
                "EXPIRED",
                at_utc=now,
                reason="FILL_RISK_VETO:" + ";".join(recheck.reasons),
            )
            return None
        previous_open = earliest - pd.Timedelta(seconds=seconds)
        order_spec = SimulatedOrder(
            contract.market,
            intent["side"],
            intent["quantity"],
            previous_open.isoformat(),
            earliest.isoformat(),
        )
        row = bars.iloc[-1]
        bar = {"timestamp": opened.isoformat(), **{c: str(row[c]) for c in row.index}}
        try:
            fill = estimate_fill(
                order_spec,
                bar,
                contract,
                market_state.costs,
                market_state.dataset.provenance[0]["contract_metadata"],
            )
        except ValueError as exc:
            self.store.transition(
                account,
                intent_id,
                "CANCELLED",
                at_utc=now,
                reason="NO_CONSERVATIVE_FILL:" + str(exc),
            )
            return None
        self.store.fill(
            account, intent_id, fill, finalized_at_utc=now, risk_recheck=recheck.to_dict()
        )
        return next(
            f["data"] for f in self.store.inspect(account)["fills"] if f["intent_id"] == intent_id
        )
