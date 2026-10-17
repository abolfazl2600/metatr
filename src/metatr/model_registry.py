"""Immutable, research-only strategy version registry for exact futures markets.

This module does not instantiate strategies, evaluate code, or grant trade permissions.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Use the existing market identity; do not introduce a second, incompatible key type.
from metatr.futures_core import Market

DEFAULT_REGISTRY_PATH = Path("data/research/registry.sqlite")
SCHEMA_VERSION = 1
_APPLICATION_ID = 0x4D545252  # MTRR; deliberately different from futures.sqlite v1.
_ALLOWED_STATUSES = frozenset({"draft", "research", "retired"})
_ALLOWED_FEATURES = frozenset({"open", "high", "low", "close", "volume"})
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z"
)


class RegistryError(ValueError):
    """An invalid registration, unknown market, or incompatible schema."""


class VersionConflict(RegistryError):
    """The requested immutable strategy version already has different content."""


class SchemaVersionError(RegistryError):
    """The database is not an understood registry schema."""


class RegistrationNotFound(RegistryError):
    """No version is bound to the requested exact market identity."""


def _canonical(value: Any) -> str:
    try:
        result = json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False)
        # Ensure no custom encoder or tuple-like surprises; require JSON-compatible content.
        if json.loads(result) != value:
            raise RegistryError("Configuration must contain plain JSON values")
        return result
    except (TypeError, ValueError, OverflowError) as exc:
        raise RegistryError("Configuration must contain finite, JSON-serializable values") from exc


def _validate_market(market: Market) -> None:
    if not isinstance(market, Market):
        raise RegistryError("Market must be a metatr.futures_core.Market")
    for field in ("exchange", "asset", "instrument", "product_id", "timeframe"):
        value = getattr(market, field)
        if (not isinstance(value, str) or not value or value != value.strip()
                or any(character in value for character in ("*", ":", "?"))
                or not value.isprintable()):
            raise RegistryError(f"Market requires an exact, non-wildcard {field}")


def _validate_id_and_version(strategy_id: str, version: str) -> None:
    if not isinstance(strategy_id, str) or not _IDENTIFIER.fullmatch(strategy_id):
        raise RegistryError("strategy_id must be a lowercase identifier")
    match = _SEMVER.fullmatch(version) if isinstance(version, str) else None
    if not match or (match[4] and any(
            len(segment) > 1 and segment[0] == "0" and segment.isdecimal()
            for segment in match[4].split("."))):
        raise RegistryError("version must follow SemVer 2.0.0")


def _required_warmup(implementation_key: str, parameters: Mapping[str, Any]) -> int:
    # Strict allowlist. Never resolve a module path from untrusted configuration.
    if implementation_key == "ema_cross":
        if set(parameters) != {"fast", "slow"}:
            raise RegistryError("ema_cross requires only fast and slow")
        fast, slow = parameters["fast"], parameters["slow"]
        if (type(fast) is not int or type(slow) is not int
                or not 1 <= fast < slow <= 300):
            raise RegistryError("Invalid EMA periods")
        return slow + 2
    if implementation_key == "momentum":
        if set(parameters) != {"lookback", "threshold"}:
            raise RegistryError("momentum requires only lookback and threshold")
        lookback, threshold = parameters["lookback"], parameters["threshold"]
        if (type(lookback) is not int or type(threshold) not in (int, float)
                or not 1 <= lookback <= 200 or not 0 <= threshold <= 1):
            raise RegistryError("Invalid momentum parameters")
        return lookback + 1
    raise RegistryError(f"Unknown implementation key: {implementation_key!r}")


def _validate_evaluation(value: Mapping[str, Any]) -> None:
    allowed = {"evaluation_bars", "min_trades", "round_trip_cost_bps"}
    if not isinstance(value, dict) or not set(value) <= allowed:
        raise RegistryError("Unknown evaluation setting")
    for name in ("evaluation_bars", "min_trades"):
        if name in value and type(value[name]) is not int:
            raise RegistryError(f"{name} must be an integer")
    if "evaluation_bars" in value and not 20 <= value["evaluation_bars"] <= 200:
        raise RegistryError("evaluation_bars must be within 20..200")
    if "min_trades" in value and value["min_trades"] < 1:
        raise RegistryError("min_trades must be positive")
    if "round_trip_cost_bps" in value:
        cost = value["round_trip_cost_bps"]
        if type(cost) not in (int, float) or not 0 <= cost <= 1000:
            raise RegistryError("Invalid cost assumption")


def _utc_timestamp(value: str | datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RegistryError("Invalid created_at timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RegistryError("created_at must have an explicit UTC time zone")
    if value.utcoffset().total_seconds() != 0:
        raise RegistryError("created_at must be in UTC")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class StrategySpec:
    """Immutable model configuration. JSON properties return copies, never shared dicts."""

    market: Market
    strategy_id: str
    version: str
    implementation_key: str
    parameters_json: str
    features: tuple[str, ...]
    warmup_bars: int
    evaluation_json: str
    created_at: str
    status: str
    config_sha256: str

    @classmethod
    def create(
        cls, market: Market, strategy_id: str, version: str,
        implementation_key: str, parameters: Mapping[str, Any], *,
        features: tuple[str, ...] = ("close",), warmup_bars: int | None = None,
        evaluation: Mapping[str, Any] | None = None,
        created_at: str | datetime | None = None, status: str = "draft",
    ) -> StrategySpec:
        _validate_market(market)
        _validate_id_and_version(strategy_id, version)
        if not isinstance(parameters, dict):
            raise RegistryError("parameters must be a JSON object")
        parameters_json = _canonical(parameters)
        params = json.loads(parameters_json)
        minimum = _required_warmup(implementation_key, params)
        if warmup_bars is None:
            warmup_bars = minimum
        if type(warmup_bars) is not int or warmup_bars < minimum:
            raise RegistryError(f"warmup_bars must be at least {minimum}")
        if not isinstance(features, (tuple, list)) or not features:
            raise RegistryError("features must be a nonempty list")
        if (any(not isinstance(item, str) or item not in _ALLOWED_FEATURES
                for item in features) or len(set(features)) != len(features)
                or "close" not in features):
            raise RegistryError("Invalid feature requirements")
        features_tuple = tuple(sorted(features))
        eval_data = dict(evaluation) if isinstance(evaluation, Mapping) else evaluation
        if eval_data is None:
            eval_data = {}
        _validate_evaluation(eval_data)
        evaluation_json = _canonical(eval_data)
        if status not in _ALLOWED_STATUSES:
            raise RegistryError("Invalid status")
        timestamp = _utc_timestamp(created_at)
        body = {
            "market": {name: getattr(market, name) for name in
                       ("exchange", "asset", "instrument", "product_id", "timeframe")},
            "strategy_id": strategy_id,
            "version": version,
            "implementation_key": implementation_key,
            "parameters": params,
            "features": list(features_tuple),
            "warmup_bars": warmup_bars,
            "evaluation": json.loads(evaluation_json),
        }
        digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
        return cls(market, strategy_id, version, implementation_key, parameters_json,
                   features_tuple, warmup_bars, evaluation_json, timestamp, status, digest)

    @property
    def parameters(self) -> dict[str, Any]:
        return json.loads(self.parameters_json)

    @property
    def evaluation(self) -> dict[str, Any]:
        return json.loads(self.evaluation_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": {part: getattr(self.market, part) for part in
                       ("exchange", "asset", "instrument", "product_id", "timeframe")},
            "strategy_id": self.strategy_id, "version": self.version,
            "implementation_key": self.implementation_key,
            "parameters": self.parameters, "features": list(self.features),
            "warmup_bars": self.warmup_bars, "evaluation": self.evaluation,
            "created_at": self.created_at, "status": self.status,
            "config_sha256": self.config_sha256, "execution": "DISABLED",
        }


def _from_row(row: sqlite3.Row) -> StrategySpec:
    market = Market(*(row[name] for name in
                      ("exchange", "asset", "instrument", "product_id", "timeframe")))
    spec = StrategySpec.create(
        market, row["strategy_id"], row["version"], row["implementation_key"],
        json.loads(row["parameters_json"]), features=tuple(json.loads(row["features_json"])),
        warmup_bars=row["warmup_bars"], evaluation=json.loads(row["evaluation_json"]),
        created_at=row["created_at"], status=row["status"],
    )
    if (spec.config_sha256 != row["config_sha256"]
            or spec.parameters_json != row["parameters_json"]
            or spec.evaluation_json != row["evaluation_json"]
            or _canonical(list(spec.features)) != row["features_json"]):
        raise RegistryError("Stored strategy integrity check failed")
    return spec


class StrategyRegistry:
    """Versioned SQLite registry; every operation uses exact five-part market keys."""

    def __init__(self, path: str | Path = DEFAULT_REGISTRY_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(check=False) as db:
            db.execute("BEGIN IMMEDIATE")
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            user_version = db.execute("PRAGMA user_version").fetchone()[0]
            objects = db.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if not objects and app_id == 0 and user_version == 0:
                self._migrate_initial(db)
            else:
                self._check_version(db, app_id, user_version)

    def _connect(self, *, check: bool = True) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        if check:
            try:
                self._check_version(db)
            except Exception:
                db.close()
                raise
        return db

    @staticmethod
    def _migrate_initial(db: sqlite3.Connection) -> None:
        db.execute("""
            CREATE TABLE schema_version (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL CHECK (version = 1)
            )
        """)
        db.execute("INSERT INTO schema_version VALUES (1, 1)")
        db.execute("""
            CREATE TABLE strategy_versions (
                exchange TEXT NOT NULL, asset TEXT NOT NULL,
                instrument TEXT NOT NULL, product_id TEXT NOT NULL,
                timeframe TEXT NOT NULL, strategy_id TEXT NOT NULL,
                version TEXT NOT NULL, implementation_key TEXT NOT NULL,
                parameters_json TEXT NOT NULL, features_json TEXT NOT NULL,
                warmup_bars INTEGER NOT NULL CHECK (warmup_bars >= 1),
                evaluation_json TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('draft', 'research', 'retired')),
                config_sha256 TEXT NOT NULL,
                PRIMARY KEY (exchange, asset, instrument, product_id,
                             timeframe, strategy_id, version)
            ) WITHOUT ROWID
        """)
        db.execute("""
            CREATE TRIGGER immutable_strategy_content
            BEFORE UPDATE OF exchange, asset, instrument, product_id, timeframe,
                strategy_id, version, implementation_key, parameters_json, features_json,
                warmup_bars, evaluation_json, created_at, config_sha256
            ON strategy_versions
            BEGIN SELECT RAISE(ABORT, 'immutable strategy version'); END
        """)
        db.execute("""
            CREATE TRIGGER no_delete_strategy_version
            BEFORE DELETE ON strategy_versions
            BEGIN SELECT RAISE(ABORT, 'immutable strategy version'); END
        """)
        db.execute("""
            CREATE TRIGGER monotonic_strategy_status
            BEFORE UPDATE OF status ON strategy_versions
            WHEN (OLD.status = 'retired' AND NEW.status != 'retired')
                OR (OLD.status = 'research' AND NEW.status = 'draft')
            BEGIN SELECT RAISE(ABORT, 'invalid strategy status transition'); END
        """)
        db.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _check_version(db: sqlite3.Connection, app_id: int | None = None,
                       user_version: int | None = None) -> None:
        if app_id is None:
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
        if user_version is None:
            user_version = db.execute("PRAGMA user_version").fetchone()[0]
        if app_id != _APPLICATION_ID or user_version != SCHEMA_VERSION:
            raise SchemaVersionError(
                "Unknown registry format or schema version; refusing migration"
            )
        try:
            row = db.execute(
                "SELECT version FROM schema_version WHERE singleton = 1"
            ).fetchone()
            if row is None or row[0] != SCHEMA_VERSION:
                raise SchemaVersionError("Registry schema_version is incompatible")
            db.execute("SELECT config_sha256 FROM strategy_versions LIMIT 0")
        except sqlite3.DatabaseError as exc:
            raise SchemaVersionError("Registry schema is incomplete") from exc

    @staticmethod
    def _key(market: Market, strategy_id: str, version: str) -> tuple[str, ...]:
        _validate_market(market)
        _validate_id_and_version(strategy_id, version)
        return (market.exchange, market.asset, market.instrument, market.product_id,
                market.timeframe, strategy_id, version)

    def register(self, spec: StrategySpec) -> StrategySpec:
        return self.register_many([spec])[0]

    def register_many(self, specs: list[StrategySpec]) -> list[StrategySpec]:
        """Atomically insert a batch or leave the DB unchanged on any conflict."""
        prepared: list[tuple[StrategySpec, tuple[str, ...]]] = []
        for spec in specs:
            if not isinstance(spec, StrategySpec):
                raise RegistryError("Expected a StrategySpec")
            # Validate even manually constructed dataclasses, not just .create().
            rebuilt = StrategySpec.create(
                spec.market, spec.strategy_id, spec.version, spec.implementation_key,
                spec.parameters, features=spec.features, warmup_bars=spec.warmup_bars,
                evaluation=spec.evaluation, created_at=spec.created_at, status=spec.status,
            )
            if rebuilt != spec:
                raise RegistryError("Invalid or modified StrategySpec")
            prepared.append((spec, self._key(spec.market, spec.strategy_id, spec.version)))
        if not prepared:
            return []
        results: list[StrategySpec] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for spec, key in prepared:
                existing = db.execute("""
                    SELECT * FROM strategy_versions
                    WHERE exchange=? AND asset=? AND instrument=? AND product_id=?
                        AND timeframe=? AND strategy_id=? AND version=?
                """, key).fetchone()
                if existing is not None:
                    current = _from_row(existing)
                    if current.config_sha256 != spec.config_sha256:
                        raise VersionConflict(
                            "Same market / strategy / version has different content"
                        )
                    results.append(current)  # Also idempotent after retirement.
                    continue
                db.execute("""
                    INSERT INTO strategy_versions (
                        exchange, asset, instrument, product_id, timeframe, strategy_id,
                        version, implementation_key, parameters_json, features_json,
                        warmup_bars, evaluation_json, created_at, status, config_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (*key, spec.implementation_key, spec.parameters_json,
                      _canonical(list(spec.features)), spec.warmup_bars, spec.evaluation_json,
                      spec.created_at, spec.status, spec.config_sha256))
                results.append(spec)
        return results

    def get(self, market: Market, strategy_id: str, version: str) -> StrategySpec | None:
        key = self._key(market, strategy_id, version)
        with self._connect() as db:
            row = db.execute("""
                SELECT * FROM strategy_versions WHERE exchange=? AND asset=? AND instrument=?
                    AND product_id=? AND timeframe=? AND strategy_id=? AND version=?
            """, key).fetchone()
            return _from_row(row) if row else None

    def resolve(self, market: Market, strategy_id: str, version: str) -> StrategySpec:
        """Only an explicitly pinned, exact market and version may resolve."""
        spec = self.get(market, strategy_id, version)
        if spec is None:
            raise RegistrationNotFound(f"No exact registration for {market.key} / "
                                       f"{strategy_id}@{version}")
        return spec

    def list(self, market: Market | None = None, *, strategy_id: str | None = None,
             status: str | None = None) -> list[StrategySpec]:
        conditions: list[str] = []
        params: list[str] = []
        if market is not None:
            _validate_market(market)
            conditions.append("exchange=? AND asset=? AND instrument=? AND product_id=? "
                              "AND timeframe=?")
            params.extend((market.exchange, market.asset, market.instrument,
                           market.product_id, market.timeframe))
        if strategy_id is not None:
            if not _IDENTIFIER.fullmatch(strategy_id):
                raise RegistryError("Invalid strategy_id filter")
            conditions.append("strategy_id=?")
            params.append(strategy_id)
        if status is not None:
            if status not in _ALLOWED_STATUSES:
                raise RegistryError("Invalid status filter")
            conditions.append("status=?")
            params.append(status)
        sql = "SELECT * FROM strategy_versions"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += (" ORDER BY exchange, asset, instrument, product_id, timeframe, "
                "strategy_id, version")
        with self._connect() as db:
            return [_from_row(row) for row in db.execute(sql, params)]

    def retire(self, market: Market, strategy_id: str, version: str) -> StrategySpec:
        key = self._key(market, strategy_id, version)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT * FROM strategy_versions WHERE exchange=? AND asset=? AND instrument=?
                    AND product_id=? AND timeframe=? AND strategy_id=? AND version=?
            """, key).fetchone()
            if row is None:
                raise RegistrationNotFound("Cannot retire an unknown exact registration")
            if row["status"] != "retired":
                db.execute("""
                    UPDATE strategy_versions SET status='retired'
                    WHERE exchange=? AND asset=? AND instrument=? AND product_id=?
                        AND timeframe=? AND strategy_id=? AND version=?
                """, key)
            return replace(_from_row(row), status="retired")


def import_legacy_config(
    registry: StrategyRegistry, market: Market,
    config_path: str | Path = Path("config/markets.json"), *,
    allow_wildcard: bool = False, version: str = "1.0.0",
) -> list[StrategySpec]:
    """Import legacy profiles only for an explicit native contract and timeframe.

    A wildcard is a template opt-in, never a persisted wildcard market key.
    Each model is inserted idempotently; an existing different version is not overwritten.
    """
    _validate_market(market)
    configuration = json.loads(Path(config_path).read_text(encoding="utf-8"))
    profiles = configuration["profiles"]
    if not isinstance(profiles, dict):
        raise RegistryError("Legacy profiles must be an object")
    if market.key in profiles:
        profile = profiles[market.key]
    else:
        wildcard = (f"{market.exchange}:{market.asset}:"
                    f"{market.instrument}:*:{market.timeframe}")
        if not allow_wildcard or wildcard not in profiles:
            raise RegistrationNotFound("No exact profile; wildcard template not enabled")
        profile = profiles[wildcard]
    if not isinstance(profile, dict) or not isinstance(profile.get("models"), list):
        raise RegistryError("Legacy profile must contain models")
    if not profile["models"]:
        raise RegistryError("Legacy profile contains no models")
    evaluation = {name: profile[name] for name in
                  ("evaluation_bars", "min_trades", "round_trip_cost_bps")
                  if name in profile}
    # Validate every entry before any insert; conflicts do not mutate prior versions.
    specs = []
    for item in profile["models"]:
        if not isinstance(item, dict) or set(item) != {"name", "parameters"}:
            raise RegistryError("Malformed legacy model")
        specs.append(StrategySpec.create(
            market, strategy_id=item["name"], version=version,
            implementation_key=item["name"], parameters=item["parameters"],
            evaluation=evaluation,
        ))
    return registry.register_many(specs)
