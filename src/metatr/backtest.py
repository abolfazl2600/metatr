"""Deterministic next-bar event simulation on quality-gated futures datasets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext

import pandas as pd

from metatr.contract_economics import (
    ContractSpec,
    CostScenario,
    SimulatedFill,
    SimulatedOrder,
    decimal,
    estimate_fill,
    number,
    value_position,
)
from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.model_registry import StrategySpec
from metatr.research import ResearchDataset, canonical, digest
from metatr.strategies import StrategyContext, predict, utc


@dataclass(frozen=True)
class SimulationRisk:
    initial_equity: str
    contracts: str
    max_notional: str
    max_drawdown_fraction: str
    event_policy: str = "IGNORE_SAME_DIRECTION_REVERSE_OPPOSITE"
    gap_policy: str = "HALT"
    max_holding_bars: int | None = None

    def validate(self):
        if (min(decimal(self.initial_equity), decimal(self.contracts),
                decimal(self.max_notional)) <= 0
                or not 0 < decimal(self.max_drawdown_fraction) <= 1
                or self.event_policy != "IGNORE_SAME_DIRECTION_REVERSE_OPPOSITE"
                or self.gap_policy != "HALT"
                or (self.max_holding_bars is not None and (
                    type(self.max_holding_bars) is not int or self.max_holding_bars < 1))):
            raise ValueError("Invalid/unsupported simulation risk or event policy")


@dataclass(frozen=True)
class FuturesLedger:
    cash: str
    contracts: str = "0"
    average_entry: str = "0"
    realized_pnl: str = "0"
    fees: str = "0"
    funding: str = "0"
    open_costs: str = "0"


def apply_fill(ledger: FuturesLedger, fill: SimulatedFill, spec: ContractSpec
               ) -> tuple[FuturesLedger, dict | None]:
    """Exact netting, entry/exit fee attribution and reversal accounting."""
    if fill.market != spec.market.key or fill.contract_sha256 != spec.fingerprint:
        raise ValueError("Fill/ledger contract mismatch")
    with localcontext() as ctx:
        ctx.prec = 40
        old, entry = decimal(ledger.contracts), decimal(ledger.average_entry)
        size, price, fee = map(decimal, (fill.quantity, fill.price, fill.fee))
        if (size < decimal(spec.min_quantity) or price <= 0 or fee < 0
                or size % decimal(spec.quantity_step) or price % decimal(spec.tick_size)
                or fill.side not in ("BUY", "SELL") or fill.fee_currency != spec.settlement_currency
                or not fill.simulated
                or decimal(fill.notional) != size * price * decimal(spec.multiplier)):
            raise ValueError("Invalid fill")
        signed = size if fill.side == "BUY" else -size
        closing = min(abs(old), size) if old * signed < 0 else Decimal(0)
        opening = size - closing
        realized = closing * decimal(spec.multiplier) * (price - entry) * (
            1 if old > 0 else -1)
        exit_fee = fee * closing / size
        allocated = decimal(ledger.open_costs) * closing / abs(old) if old else Decimal(0)
        remaining_costs = decimal(ledger.open_costs) - allocated + fee - exit_fee
        new = old + signed
        if not new:
            new_entry = Decimal(0)
        elif old * signed >= 0:
            new_entry = (abs(old) * entry + size * price) / abs(new)
        elif opening:
            new_entry = price
        else:
            new_entry = entry
        result = FuturesLedger(
            number(decimal(ledger.cash) + realized - fee), number(new), number(new_entry),
            number(decimal(ledger.realized_pnl) + realized),
            number(decimal(ledger.fees) + fee), ledger.funding, number(remaining_costs))
        trade = ({"timestamp": fill.timestamp, "quantity": number(closing),
                  "direction": "LONG" if old > 0 else "SHORT", "entry": number(entry),
                  "exit": number(price), "gross_pnl": number(realized),
                  "costs": number(exit_fee + allocated),
                  "net_pnl": number(realized - exit_fee - allocated)} if closing else None)
        return result, trade


def mark_ledger(ledger: FuturesLedger, mark: str, spec: ContractSpec) -> dict:
    if decimal(ledger.contracts):
        value = value_position(ledger.contracts, ledger.average_entry, mark, spec)
    else:
        value = {"notional": "0", "unrealized_pnl": "0"}
    return {**value, "cash": ledger.cash, "contracts": ledger.contracts,
            "equity": number(decimal(ledger.cash) + decimal(value["unrealized_pnl"]))}


@dataclass(frozen=True)
class BacktestResult:
    result_json: str

    def to_dict(self) -> dict:
        return json.loads(self.result_json)


class BacktestEngine:
    def run(self, dataset: ResearchDataset, strategy_version: StrategySpec,
            contract_spec: ContractSpec, cost_scenario: CostScenario,
            risk_config: SimulationRisk, seed: int = 0, *, decision_start: int = 0,
            decision_end: int | None = None, baseline: str | None = None) -> BacktestResult:
        """Finalize simulated next-open fills only when the next bar has closed.

        Warmup prefix never trades before decision_start. Decision_end is exclusive;
        it reserves a next-bar exit for fold settlement under an explicit policy.
        """
        dataset.verify()
        report = json.loads(dataset.validation_json)
        if not report or not report["ready"] or report["data_sha256"] != dataset.data_sha256:
            raise ValueError("Quality-gated dataset required")
        pinned = report.pop("report_sha256")
        if digest(report) != pinned or report["market"] != dataset.market.key:
            raise ValueError("Invalid dataset report fingerprint")
        if (dataset.market != strategy_version.market or contract_spec.market != dataset.market
                or strategy_version.status == "retired"):
            raise ValueError("Exact active strategy/contract market required")
        calendar = report["calendar"]
        if (not calendar or calendar["calendar_id"] != contract_spec.calendar_id
                or calendar["version"] != contract_spec.calendar_version):
            raise ValueError("Matching sourced contract calendar required for simulation")
        risk_config.validate()
        bars = dataset.bars
        seconds = TIMEFRAME_SECONDS[dataset.market.timeframe]
        if ((bars.index.asi8[1:] - bars.index.asi8[:-1]) != seconds * 10**9).any():
            raise ValueError("Simulation gap/session carry policy unsupported; halt")
        if not 0 <= decision_start < len(bars):
            raise ValueError("Invalid simulation decision range")
        decision_end = len(bars) if decision_end is None else decision_end
        if not decision_start < decision_end <= len(bars) or baseline not in (
                None, "flat", "buy_hold"):
            raise ValueError("Invalid settlement or benchmark policy")
        metadata = dataset.provenance[0]["contract_metadata"]
        for item in dataset.provenance:
            contract_spec.validate(item["contract_metadata"], bars.index[0])
        cost_scenario.validate(contract_spec, bars.index[0])
        ledger = FuturesLedger(risk_config.initial_equity)
        peak = decimal(ledger.cash)
        pending = None
        entry_pos = None
        events, fills, trades, equity, decisions = [], [], [], [], []
        status = "COMPLETE"
        identity = {"market": dataset.market.key, "strategy": strategy_version.config_sha256,
                    "contract": contract_spec.fingerprint, "costs": cost_scenario.fingerprint,
                    "risk": asdict(risk_config), "seed": seed, "baseline": baseline}

        def event(kind, at, payload):
            body = {"kind": kind, "timestamp": at, "payload": payload}
            events.append({"event_id": digest({**identity, **body}), **body})

        with localcontext() as ctx:
            ctx.prec = 40
            for pos, (opened, row) in enumerate(bars.iterrows()):
                closed = opened + pd.Timedelta(seconds=seconds)
                contract_spec.validate(metadata, closed)
                if pos < decision_start:
                    continue
                bar = {"timestamp": opened.isoformat(), **{c: str(row[c]) for c in row.index}}
                if pending is not None:
                    try:
                        fill = estimate_fill(pending, bar, contract_spec, cost_scenario, metadata)
                        projected = decimal(ledger.contracts) + (
                            decimal(fill.quantity) * (1 if fill.side == "BUY" else -1))
                        notional = abs(projected) * decimal(contract_spec.multiplier) * decimal(
                            fill.price)
                        if notional + decimal(fill.fee) > decimal(risk_config.max_notional):
                            raise ValueError("MAX_NOTIONAL_WITH_FEE_BUFFER")
                        old_quantity = decimal(ledger.contracts)
                        ledger, trade = apply_fill(ledger, fill, contract_spec)
                        new_quantity = decimal(ledger.contracts)
                        if not new_quantity:
                            entry_pos = None
                        elif not old_quantity or old_quantity * new_quantity < 0:
                            entry_pos = pos
                        fill_body = asdict(fill)
                        fill_body["finalized_at_utc"] = closed.isoformat()
                        fill_body["fill_id"] = digest({**identity, **fill_body})
                        fills.append(fill_body)
                        if trade:
                            trade["fill_id"] = fill_body["fill_id"]
                            trades.append(trade)
                        event("SIMULATED_FILL", closed.isoformat(), fill_body)
                    except ValueError as exc:
                        event("FILL_REJECTED", closed.isoformat(), {"reason": str(exc)})
                    pending = None
                marked = mark_ledger(ledger, str(row.close), contract_spec)
                funding = decimal(marked["notional"]) * decimal(
                    cost_scenario.funding_bps_per_bar) / 10000
                if funding:
                    ledger = replace(ledger, cash=number(decimal(ledger.cash) - funding),
                                     funding=number(decimal(ledger.funding) + funding),
                                     open_costs=number(decimal(ledger.open_costs) + funding))
                    event("ASSUMED_FUNDING", closed.isoformat(), {"charge": number(funding)})
                    marked = mark_ledger(ledger, str(row.close), contract_spec)
                peak = max(peak, decimal(marked["equity"]))
                drawdown = (peak - decimal(marked["equity"])) / peak
                equity.append({"timestamp": closed.isoformat(), **marked,
                               "drawdown": number(drawdown), "fees": ledger.fees,
                               "funding": ledger.funding})
                event("BAR_CLOSE", closed.isoformat(), marked)
                if decimal(marked["equity"]) <= 0 or drawdown >= decimal(
                        risk_config.max_drawdown_fraction):
                    status = "HALTED"
                    event("RISK_HALT", closed.isoformat(), {"reason": "EQUITY_OR_DRAWDOWN"})
                    break
                context = StrategyContext(dataset.market, opened, closed, utc(dataset.asof_utc),
                                          dataset.snapshot_ids, dataset.data_sha256,
                                          strategy_version.warmup_bars, seed)
                decision = predict(bars.iloc[:pos + 1], strategy_version, context)
                body = decision.to_dict()
                if baseline is not None:
                    body["action"] = ("LONG" if baseline == "buy_hold"
                                      and pos == decision_start else "NO_TRADE")
                    body["reason"] = f"explicit {baseline} benchmark"
                decisions.append(body)
                event("DECISION", closed.isoformat(), body)
                side = {"LONG": 1, "SHORT": -1, "NO_TRADE": 0}[body["action"]]
                current = decimal(ledger.contracts)
                target = decimal(risk_config.contracts) * side
                # At the fold boundary, explicitly settle at the *next* bar, never same close.
                settling = pos == decision_end - 1 and decision_end < len(bars)
                horizon_exit = (baseline != "buy_hold" and entry_pos is not None
                                and risk_config.max_holding_bars is not None
                                and pos - entry_pos + 1 >= risk_config.max_holding_bars)
                if settling or horizon_exit:
                    target = Decimal(0)
                elif pos >= decision_end or not side or current * side > 0:
                    continue
                delta = target - current
                if delta:
                    pending = SimulatedOrder(dataset.market, "BUY" if delta > 0 else "SELL",
                                             number(abs(delta)), opened.isoformat(),
                                             closed.isoformat())
                    event("INTENT", closed.isoformat(), asdict(pending))
            if pending:
                event("INTENT_EXPIRED", equity[-1]["timestamp"], {"reason": "NO_NEXT_BAR"})
        core = {"schema_version": 1, "execution": "DISABLED", "research_only": True,
                "simulated": True, "status": status, "market": dataset.market.key,
                "data_sha256": dataset.data_sha256, "dataset_report_sha256": pinned,
                "snapshot_ids": dataset.snapshot_ids, "strategy": strategy_version.to_dict(),
                "contract_spec": asdict(contract_spec), "cost_scenario": asdict(cost_scenario),
                "risk": asdict(risk_config), "seed": seed,
                "decision_start": decision_start, "decision_end": decision_end,
                "baseline": baseline, "decisions": decisions, "events": events,
                "fills": fills, "trades": trades, "equity": equity, "ledger": asdict(ledger)}
        return BacktestResult(canonical({"run_id": digest(core), **core}))
