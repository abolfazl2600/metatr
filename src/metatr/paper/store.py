"""Append-only paper journal with atomic Decimal ledger projections and reconciliation."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import localcontext
from pathlib import Path

from metatr.backtest import FuturesLedger, apply_fill, mark_ledger
from metatr.calendars import stamp
from metatr.contract_economics import ContractSpec, CostScenario, SimulatedFill, decimal, number
from metatr.futures_core import Market
from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.research import canonical, digest, parse_market

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4D545250
TABLES = (
    "paper_accounts",
    "paper_runs",
    "intents",
    "paper_orders",
    "paper_fills",
    "positions",
    "cash_ledger",
    "equity_marks",
    "risk_events",
    "kill_switch_events",
)
SCHEMA = """
CREATE TABLE paper_accounts(account TEXT PRIMARY KEY, market TEXT NOT NULL, currency TEXT NOT NULL,
 initial_equity TEXT NOT NULL, ledger_json TEXT NOT NULL, halted INTEGER NOT NULL);
CREATE TABLE paper_runs(run_id TEXT PRIMARY KEY,account TEXT NOT NULL REFERENCES paper_accounts,
 pins_json TEXT NOT NULL);
CREATE TABLE intents(intent_id TEXT PRIMARY KEY,account TEXT NOT NULL REFERENCES paper_accounts,
 run_id TEXT NOT NULL REFERENCES paper_runs,market TEXT NOT NULL,strategy TEXT NOT NULL,
 closed_bar_utc TEXT NOT NULL,side TEXT NOT NULL,quantity TEXT NOT NULL,intent_type TEXT NOT NULL,
 payload_json TEXT NOT NULL,UNIQUE(account,market,strategy,closed_bar_utc,intent_type));
CREATE TABLE paper_orders(intent_id TEXT PRIMARY KEY REFERENCES intents,account TEXT NOT NULL,
 status TEXT NOT NULL,approval_json TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE paper_fills(intent_id TEXT PRIMARY KEY REFERENCES paper_orders,account TEXT NOT NULL,
 event_id TEXT UNIQUE NOT NULL,fill_json TEXT NOT NULL);
CREATE TABLE positions(account TEXT NOT NULL REFERENCES paper_accounts,market TEXT NOT NULL,
 ledger_json TEXT NOT NULL,PRIMARY KEY(account,market));
CREATE TABLE cash_ledger(event_id TEXT PRIMARY KEY,account TEXT NOT NULL REFERENCES paper_accounts,
 amount TEXT NOT NULL,currency TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE equity_marks(event_id TEXT PRIMARY KEY,account TEXT NOT NULL REFERENCES paper_accounts,
 timestamp TEXT NOT NULL,mark_json TEXT NOT NULL,UNIQUE(account,timestamp));
CREATE TABLE risk_events(event_id TEXT PRIMARY KEY,account TEXT NOT NULL REFERENCES paper_accounts,
 decision_json TEXT NOT NULL);
CREATE TABLE kill_switch_events(event_id TEXT PRIMARY KEY,
 account TEXT NOT NULL REFERENCES paper_accounts,
 timestamp TEXT NOT NULL,halted INTEGER NOT NULL,reason TEXT NOT NULL);
CREATE TABLE audit_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,
 account TEXT NOT NULL,kind TEXT NOT NULL,occurred_at TEXT NOT NULL,payload_json TEXT NOT NULL,
 previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
 BEGIN SELECT RAISE(ABORT,'append-only paper journal'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
 BEGIN SELECT RAISE(ABORT,'append-only paper journal'); END;
"""


class PaperStore:
    def __init__(self, path: str | Path = "data/paper/paper.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            app = db.execute("PRAGMA application_id").fetchone()[0]
            objects = db.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version == 0 and app == 0 and not objects:
                db.executescript(SCHEMA)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            elif version != SCHEMA_VERSION or app != APPLICATION_ID:
                raise ValueError("Unknown or non-paper SQLite schema")
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
            if not set((*TABLES, "audit_events", "audit_no_update", "audit_no_delete")) <= names:
                raise ValueError("Incomplete paper SQLite schema/append-only guards")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Paper SQLite foreign-key inconsistency")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _account(db, account):
        row = db.execute("SELECT * FROM paper_accounts WHERE account=?", (account,)).fetchone()
        if row is None:
            raise ValueError("Unknown paper account")
        return dict(row)

    @staticmethod
    def _run(db, run_id):
        row = db.execute("SELECT * FROM paper_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown paper run")
        return row["account"], json.loads(row["pins_json"])

    @staticmethod
    def _contract(body):
        body = dict(body)
        body["market"] = Market(**body["market"])
        return ContractSpec(**body)

    @staticmethod
    def _save_ledger(db, account, ledger):
        encoded = canonical(asdict(ledger))
        db.execute("UPDATE paper_accounts SET ledger_json=? WHERE account=?", (encoded, account))
        db.execute("UPDATE positions SET ledger_json=? WHERE account=?", (encoded, account))

    @classmethod
    def _reduce(cls, db, event_id, account, kind, at, p):
        if kind == "ACCOUNT_INIT":
            if p["currency"] != "USD" or decimal(p["initial_equity"]) <= 0:
                raise ValueError("Positive simulated USD opening equity required")
            parse_market(p["market"])
            ledger = FuturesLedger(p["initial_equity"])
            encoded = canonical(asdict(ledger))
            db.execute(
                "INSERT INTO paper_accounts VALUES(?,?,?,?,?,0)",
                (account, p["market"], p["currency"], p["initial_equity"], encoded),
            )
            db.execute("INSERT INTO positions VALUES(?,?,?)", (account, p["market"], encoded))
            db.execute(
                "INSERT INTO cash_ledger VALUES(?,?,?,?,?)",
                (event_id, account, p["initial_equity"], p["currency"], "SIMULATED_OPENING"),
            )
            return
        state = cls._account(db, account)
        if kind == "RUN_INIT":
            if p["pins"]["market"] != state["market"]:
                raise ValueError("Paper account is bound to exactly one market")
            db.execute(
                "INSERT INTO paper_runs VALUES(?,?,?)", (p["run_id"], account, canonical(p["pins"]))
            )
        elif kind == "INTENT_NEW":
            owner, pins = cls._run(db, p["run_id"])
            if (
                owner != account
                or p["market"] != state["market"]
                or p["strategy"] != pins["strategy_config_sha256"]
                or p["side"] not in ("BUY", "SELL")
                or decimal(p["quantity"]) <= 0
                or p["intent_type"] != "MARKET"
            ):
                raise ValueError("Invalid paper intent identity/quantity")
            db.execute(
                "INSERT INTO intents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    p["intent_id"],
                    account,
                    p["run_id"],
                    p["market"],
                    p["strategy"],
                    p["closed_bar_utc"],
                    p["side"],
                    p["quantity"],
                    p["intent_type"],
                    canonical(p),
                ),
            )
            db.execute(
                "INSERT INTO paper_orders VALUES(?,?,?,'{}','')", (p["intent_id"], account, "NEW")
            )
        elif kind == "ORDER_TRANSITION":
            row = db.execute(
                "SELECT * FROM paper_orders WHERE intent_id=? AND account=?",
                (p["intent_id"], account),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown paper order")
            allowed = {"NEW": {"ACCEPTED", "REJECTED"}, "ACCEPTED": {"CANCELLED", "EXPIRED"}}
            if p["status"] not in allowed.get(row["status"], set()):
                raise ValueError("Illegal paper order transition")
            if p["status"] == "ACCEPTED" and (
                state["halted"] or p["approval"].get("action") != "APPROVE"
            ):
                raise ValueError("Risk approval and nonhalted account required")
            db.execute(
                "UPDATE paper_orders SET status=?,approval_json=?,reason=? WHERE intent_id=?",
                (
                    p["status"],
                    canonical(p["approval"]) if p["approval"] else row["approval_json"],
                    p["reason"],
                    p["intent_id"],
                ),
            )
        elif kind == "FILL":
            order = db.execute(
                """SELECT i.*,o.status,o.approval_json FROM intents i JOIN paper_orders o
                USING(intent_id) WHERE intent_id=? AND i.account=?""",
                (p["intent_id"], account),
            ).fetchone()
            if order is None or order["status"] != "ACCEPTED" or state["halted"]:
                raise ValueError("Accepted, nonhalted paper order required")
            _, pins = cls._run(db, order["run_id"])
            spec = cls._contract(pins["contract_spec"])
            fill = SimulatedFill(**p["fill"])
            if (
                fill.side != order["side"]
                or decimal(fill.quantity) != decimal(order["quantity"])
                or fill.cost_sha256 != pins["cost_sha256"]
                or stamp(fill.timestamp) < stamp(order["closed_bar_utc"])
                or (stamp(at) - stamp(fill.timestamp)).total_seconds()
                < TIMEFRAME_SECONDS[spec.market.timeframe]
            ):
                raise ValueError("Fill differs from approved paper intent/pins")
            proof = p.get("risk_recheck", {})
            approval = json.loads(order["approval_json"])
            if (
                proof.get("action") != "APPROVE"
                or proof.get("ledger_sha256") != digest(json.loads(state["ledger_json"]))
                or not approval.get("policy_sha256")
                or proof.get("policy_sha256") != approval["policy_sha256"]
                or stamp(proof["timestamp"]) != stamp(at)
            ):
                raise ValueError("Fresh policy-pinned risk recheck required at fill commit")
            old = FuturesLedger(**json.loads(state["ledger_json"]))
            ledger, trade = apply_fill(old, fill, spec)
            cls._save_ledger(db, account, ledger)
            db.execute(
                "UPDATE paper_orders SET status='FILLED' WHERE intent_id=?", (p["intent_id"],)
            )
            db.execute(
                "INSERT INTO paper_fills VALUES(?,?,?,?)",
                (
                    p["intent_id"],
                    account,
                    event_id,
                    canonical({"fill": p["fill"], "trade": trade, "ledger_after": asdict(ledger)}),
                ),
            )
            db.execute(
                "INSERT INTO cash_ledger VALUES(?,?,?,?,?)",
                (
                    event_id,
                    account,
                    number(decimal(ledger.cash) - decimal(old.cash)),
                    state["currency"],
                    "SIMULATED_FILL_PNL_AND_FEE",
                ),
            )
        elif kind == "MARK":
            owner, pins = cls._run(db, p["run_id"])
            if owner != account:
                raise ValueError("Run/account mismatch")
            spec = cls._contract(pins["contract_spec"])
            ledger = FuturesLedger(**json.loads(state["ledger_json"]))
            marked = mark_ledger(ledger, p["price"], spec)
            db.execute(
                "INSERT INTO equity_marks VALUES(?,?,?,?)",
                (
                    event_id,
                    account,
                    at,
                    canonical(
                        {
                            **marked,
                            "price": p["price"],
                            "fees": ledger.fees,
                            "funding": ledger.funding,
                        }
                    ),
                ),
            )
        elif kind == "FUNDING":
            owner, pins = cls._run(db, p["run_id"])
            if owner != account:
                raise ValueError("Run/account mismatch")
            spec = cls._contract(pins["contract_spec"])
            costs = CostScenario(**pins["cost_scenario"])
            costs.validate(spec, at)
            ledger = FuturesLedger(**json.loads(state["ledger_json"]))
            value = mark_ledger(ledger, p["price"], spec)
            amount = decimal(value["notional"]) * decimal(costs.funding_bps_per_bar) / 10000
            updated = replace(
                ledger,
                cash=number(decimal(ledger.cash) - amount),
                funding=number(decimal(ledger.funding) + amount),
                open_costs=number(decimal(ledger.open_costs) + amount),
            )
            cls._save_ledger(db, account, updated)
            db.execute(
                "INSERT INTO cash_ledger VALUES(?,?,?,?,?)",
                (event_id, account, number(-amount), state["currency"], "ASSUMED_FUNDING"),
            )
        elif kind == "RISK_EVENT":
            db.execute("INSERT INTO risk_events VALUES(?,?,?)", (event_id, account, canonical(p)))
        elif kind in ("HALT", "HALT_RESET"):
            if not p["reason"].strip() or (kind == "HALT_RESET" and not p.get("health_checked")):
                raise ValueError("Explicit reset reason and fresh health check required")
            halted = int(kind == "HALT")
            db.execute("UPDATE paper_accounts SET halted=? WHERE account=?", (halted, account))
            db.execute(
                "INSERT INTO kill_switch_events VALUES(?,?,?,?,?)",
                (event_id, account, at, halted, p["reason"]),
            )
        else:
            raise ValueError("Unknown paper journal event")

    def append(self, account, kind, key, payload, at_utc):
        """One journal append and every derived effect commit together, or none do."""
        at, encoded = stamp(at_utc).isoformat(), canonical(payload)
        event_id = digest({"account": account, "kind": kind, "key": key})
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM audit_events WHERE event_id=?", (event_id,)).fetchone()
            if old:
                if old["payload_json"] != encoded or old["occurred_at"] != at:
                    raise ValueError("Conflicting immutable paper event")
                return event_id
            previous = db.execute(
                "SELECT event_hash FROM audit_events WHERE account=? "
                "ORDER BY sequence DESC LIMIT 1",
                (account,),
            ).fetchone()
            prev = previous[0] if previous else "0" * 64
            body = {
                "event_id": event_id,
                "account": account,
                "kind": kind,
                "occurred_at": at,
                "payload_json": encoded,
                "previous_hash": prev,
            }
            with localcontext() as context:
                context.prec = 40
                self._reduce(db, event_id, account, kind, at, payload)
            db.execute(
                """INSERT INTO audit_events(event_id,account,kind,occurred_at,payload_json,
                          previous_hash,event_hash) VALUES(?,?,?,?,?,?,?)""",
                (event_id, account, kind, at, encoded, prev, digest(body)),
            )
        return event_id

    def initialize(self, account, market, initial_equity, *, at_utc, currency="USD"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", account):
            raise ValueError("Invalid paper account ID")
        return self.append(
            account,
            "ACCOUNT_INIT",
            account,
            {
                "market": market.key,
                "currency": currency,
                "initial_equity": number(decimal(initial_equity)),
            },
            at_utc,
        )

    def register_run(self, account, pins, *, at_utc):
        required = {
            "market",
            "strategy_config_sha256",
            "strategy_version",
            "data_sha256",
            "contract_spec",
            "contract_metadata",
            "strategy_id",
            "cost_scenario",
            "cost_sha256",
        }
        if not required <= pins.keys() or pins.get("execution") != "PAPER_ONLY":
            raise ValueError("Explicit paper run provenance required")
        spec = self._contract(pins["contract_spec"])
        costs = CostScenario(**pins["cost_scenario"])
        if costs.fingerprint != pins["cost_sha256"] or spec.market.key != pins["market"]:
            raise ValueError("Paper economics fingerprint mismatch")
        spec.validate(pins["contract_metadata"], at_utc)
        costs.validate(spec, at_utc)
        for field in ("strategy_config_sha256", "data_sha256", "cost_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", pins[field]):
                raise ValueError("Invalid paper provenance digest")
        run_id = digest({"account": account, "pins": pins})
        self.append(account, "RUN_INIT", run_id, {"run_id": run_id, "pins": pins}, at_utc)
        return run_id

    def new_intent(self, account, run_id, closed_bar_utc, side, quantity, *, data_sha256):
        state = self.inspect(account)
        pins = next((r["pins"] for r in state["runs"] if r["run_id"] == run_id), None)
        if pins is None:
            raise ValueError("Unknown paper run for account")
        closed = stamp(closed_bar_utc).isoformat()
        identity = {
            "account": account,
            "market": state["market"],
            "strategy": pins["strategy_config_sha256"],
            "closed_bar_utc": closed,
            "intent_type": "MARKET",
        }
        intent_id = digest(identity)
        payload = {
            **identity,
            "intent_id": intent_id,
            "run_id": run_id,
            "side": side,
            "quantity": number(decimal(quantity)),
            "data_sha256": data_sha256,
            "expires_at_utc": (
                stamp(closed)
                + timedelta(seconds=2 * TIMEFRAME_SECONDS[parse_market(state["market"]).timeframe])
            ).isoformat(),
        }
        existing = next((i for i in state["intents"] if i["intent_id"] == intent_id), None)
        if existing:
            if existing["side"] != side or decimal(existing["quantity"]) != decimal(quantity):
                raise ValueError("Conflicting per-bar paper intent")
            return intent_id
        self.append(account, "INTENT_NEW", intent_id, payload, closed)
        return intent_id

    def transition(self, account, intent_id, status, *, at_utc, reason, approval=None):
        return self.append(
            account,
            "ORDER_TRANSITION",
            intent_id + ":" + status,
            {
                "intent_id": intent_id,
                "status": status,
                "reason": reason,
                "approval": approval or {},
            },
            at_utc,
        )

    def fill(self, account, intent_id, fill: SimulatedFill, *, finalized_at_utc, risk_recheck=None):
        with self.connection() as db:
            existing = db.execute(
                "SELECT * FROM paper_fills WHERE account=? AND intent_id=?", (account, intent_id)
            ).fetchone()
            if existing:
                if json.loads(existing["fill_json"])["fill"] != asdict(fill):
                    raise ValueError("Conflicting duplicate paper fill")
                return existing["event_id"]
        return self.append(
            account,
            "FILL",
            intent_id,
            {"intent_id": intent_id, "fill": asdict(fill), "risk_recheck": risk_recheck or {}},
            finalized_at_utc,
        )

    def event(self, account, kind, key):
        event_id = digest({"account": account, "kind": kind, "key": key})
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM audit_events WHERE event_id=? AND account=?", (event_id, account)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def _bar_effect(self, account, kind, run_id, price, at_utc):
        key = stamp(at_utc).isoformat()
        price = number(decimal(price))
        prior = self.event(account, kind, key)
        if prior:
            with self.connection() as db:
                _, old = self._run(db, prior["payload"]["run_id"])
                owner, new = self._run(db, run_id)
            if (
                owner != account
                or decimal(prior["payload"]["price"]) != decimal(price)
                or old["contract_spec"] != new["contract_spec"]
                or old["cost_sha256"] != new["cost_sha256"]
            ):
                raise ValueError("Conflicting resumed paper bar effects")
            return prior["event_id"]
        return self.append(account, kind, key, {"run_id": run_id, "price": price}, at_utc)

    def mark(self, account, run_id, price, *, at_utc):
        return self._bar_effect(account, "MARK", run_id, price, at_utc)

    def funding(self, account, run_id, price, *, at_utc):
        return self._bar_effect(account, "FUNDING", run_id, price, at_utc)

    def halt(self, account, reason, *, at_utc):
        return self.append(
            account, "HALT", [reason, stamp(at_utc).isoformat()], {"reason": reason}, at_utc
        )

    def stop_pending(self, account, reason, *, at_utc):
        for order in self.inspect(account)["orders"]:
            if order["status"] in ("NEW", "ACCEPTED"):
                terminal = "REJECTED" if order["status"] == "NEW" else "EXPIRED"
                self.transition(account, order["intent_id"], terminal, at_utc=at_utc, reason=reason)

    def reset_halt(self, account, reason, *, at_utc, health_checked=False):
        return self.append(
            account,
            "HALT_RESET",
            [reason, stamp(at_utc).isoformat()],
            {"reason": reason, "health_checked": health_checked},
            at_utc,
        )

    def inspect(self, account):
        with self.connection() as db:
            result = self._account(db, account)
            result["ledger"] = json.loads(result.pop("ledger_json"))
            for table, key, json_column in (
                ("paper_runs", "runs", "pins_json"),
                ("intents", "intents", "payload_json"),
                ("paper_fills", "fills", "fill_json"),
                ("equity_marks", "marks", "mark_json"),
                ("risk_events", "risk_events", "decision_json"),
            ):
                rows = db.execute(
                    f"SELECT * FROM {table} WHERE account=? ORDER BY rowid", (account,)
                ).fetchall()
                result[key] = []
                for row in rows:
                    item = dict(row)
                    item["pins" if key == "runs" else "data"] = json.loads(item.pop(json_column))
                    result[key].append(item)
            result["orders"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM paper_orders WHERE account=? ORDER BY rowid", (account,)
                )
            ]
            result["cash_events"] = [
                dict(r)
                for r in db.execute(
                    "SELECT c.*,a.occurred_at FROM cash_ledger c "
                    "JOIN audit_events a USING(event_id) "
                    "WHERE c.account=? ORDER BY a.sequence",
                    (account,),
                )
            ]
            result["halt_events"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM kill_switch_events WHERE account=? ORDER BY rowid", (account,)
                )
            ]
            result["execution"] = "PAPER_ONLY"
            return result

    def reconcile(self, account):
        """Rebuild all projections from the hash-checked journal; compare without repairing."""
        memory = sqlite3.connect(":memory:")
        memory.row_factory = sqlite3.Row
        try:
            memory.executescript(SCHEMA)
            with self.connection() as db:
                db.execute("BEGIN")
                rows = db.execute(
                    "SELECT * FROM audit_events WHERE account=? ORDER BY sequence", (account,)
                ).fetchall()
                if not rows:
                    raise ValueError("Missing paper journal")
                previous = "0" * 64
                for row in rows:
                    body = {
                        k: row[k]
                        for k in (
                            "event_id",
                            "account",
                            "kind",
                            "occurred_at",
                            "payload_json",
                            "previous_hash",
                        )
                    }
                    if row["previous_hash"] != previous or digest(body) != row["event_hash"]:
                        raise ValueError("Paper journal hash chain mismatch")
                    with localcontext() as context:
                        context.prec = 40
                        self._reduce(
                            memory,
                            row["event_id"],
                            account,
                            row["kind"],
                            row["occurred_at"],
                            json.loads(row["payload_json"]),
                        )
                    previous = row["event_hash"]
                for table in TABLES:
                    actual = [
                        tuple(r)
                        for r in db.execute(f"SELECT * FROM {table} WHERE account=?", (account,))
                    ]
                    expected = [
                        tuple(r)
                        for r in memory.execute(
                            f"SELECT * FROM {table} WHERE account=?", (account,)
                        )
                    ]
                    if sorted(actual) != sorted(expected):
                        raise ValueError(f"Paper projection mismatch: {table}")
            return True
        finally:
            memory.close()
