"""Versioned, offline-first SQLite provenance store for verified market snapshots.

This module persists research data only. It has no broker keys, trading APIs,
order state, or automatic network access.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from metatr.data import validate_bars
from metatr.futures_core import Market

SCHEMA_VERSION = 1
TIMEFRAME_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900,
    "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400,
}
OHLCV = ("open", "high", "low", "close", "volume")


def _utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Snapshot time must be timezone-aware")
    return timestamp.astimezone(timezone.utc)


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class FuturesSQLiteStore:
    """Idempotent raw snapshots with immutable existing (market, candle) values.

    Raw payloads are assumed to be *public market data*. Callers must not
    persist credentials or personally identifying private exchange responses.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            current = db.execute("PRAGMA user_version").fetchone()[0]
            if current not in (0, SCHEMA_VERSION):
                raise ValueError(
                    f"Unsupported SQLite schema version {current}; expected {SCHEMA_VERSION}"
                )
            if current == 0:
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS raw_snapshots (
                        snapshot_id TEXT PRIMARY KEY,
                        market_key TEXT NOT NULL,
                        source TEXT NOT NULL,
                        received_at_utc TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        schema_version INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_snapshots_market
                        ON raw_snapshots(market_key, received_at_utc);
                    CREATE TABLE IF NOT EXISTS verified_bars (
                        market_key TEXT NOT NULL,
                        candle_open_utc TEXT NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL,
                        first_snapshot_id TEXT NOT NULL,
                        PRIMARY KEY (market_key, candle_open_utc),
                        FOREIGN KEY (first_snapshot_id)
                            REFERENCES raw_snapshots(snapshot_id)
                    );
                    PRAGMA user_version = 1;
                    """
                )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()

    def save_snapshot(
        self,
        market: Market,
        *,
        source: str,
        raw_payload: dict,
        contract_metadata: dict,
        bars: pd.DataFrame,
        asof: datetime,
    ) -> str:
        """Persist verified closed bars and their raw response atomically.

        Re-ingestion of identical data is idempotent; a conflicting OHLCV
        revision aborts the entire transaction rather than overwriting history.
        """
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Source provenance is required")
        if not isinstance(raw_payload, dict) or not isinstance(contract_metadata, dict):
            raise ValueError("Raw payload and contract metadata must be dictionaries")
        if contract_metadata.get("product_id") != market.product_id:
            raise ValueError("Contract metadata does not match market identity")
        if market.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported market timeframe")
        asof_utc = _utc(asof)
        seconds = TIMEFRAME_SECONDS[market.timeframe]
        checked = validate_bars(bars)
        if "volume" not in checked:
            raise ValueError("Futures snapshots require volume")
        opens = checked.index.view("int64") // 1_000_000_000
        if (opens % seconds != 0).any():
            raise ValueError("Candle timestamps are not aligned to timeframe")
        if (opens + seconds > int(asof_utc.timestamp())).any():
            raise ValueError("Snapshot contains a still-forming candle")
        if len(opens) > 1 and (opens[1:] - opens[:-1] != seconds).any():
            raise ValueError("Snapshot contains missing candles")

        raw = _json(raw_payload)
        meta = _json(contract_metadata)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        identity = _json({
            "market": market.key, "source": source,
            "payload_sha256": digest, "metadata": meta,
        })
        snapshot_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()

        with self._connection() as db:
            db.execute(
                """INSERT OR IGNORE INTO raw_snapshots
                   (snapshot_id, market_key, source, received_at_utc,
                    payload_sha256, payload_json, metadata_json, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, market.key, source, asof_utc.isoformat(),
                 digest, raw, meta, SCHEMA_VERSION),
            )
            for index, record in checked.iterrows():
                key = (market.key, index.isoformat())
                values = tuple(float(record[column]) for column in OHLCV)
                old = db.execute(
                    """SELECT open, high, low, close, volume FROM verified_bars
                       WHERE market_key = ? AND candle_open_utc = ?""",
                    key,
                ).fetchone()
                if old is not None:
                    if tuple(old) != values:
                        raise ValueError("Conflicting verified candle; manual review required")
                    continue
                db.execute(
                    """INSERT INTO verified_bars
                       (market_key, candle_open_utc, open, high, low, close,
                        volume, first_snapshot_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (*key, *values, snapshot_id),
                )
        return snapshot_id

    def read_bars(self, market: Market, *, limit: int = 350) -> pd.DataFrame:
        """Read last N locally stored candles; no network access."""
        if not 1 <= limit <= 100_000:
            raise ValueError("Invalid query limit")
        with self._connection() as db:
            rows = db.execute(
                """SELECT candle_open_utc, open, high, low, close, volume
                   FROM verified_bars WHERE market_key = ?
                   ORDER BY candle_open_utc DESC LIMIT ?""",
                (market.key, limit),
            ).fetchall()
        if not rows:
            raise ValueError("No stored candles for this market")
        frame = pd.DataFrame(rows, columns=("timestamp", *OHLCV))
        frame.index = pd.to_datetime(frame.pop("timestamp"), utc=True)
        return validate_bars(frame)

    def read_snapshot(self, snapshot_id: str) -> dict:
        """Recover the exact public response and its stored contract metadata."""
        with self._connection() as db:
            row = db.execute(
                """SELECT market_key, source, received_at_utc, payload_sha256,
                          payload_json, metadata_json, schema_version
                   FROM raw_snapshots WHERE snapshot_id = ?""",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown snapshot ID")
        market_key, source, received, checksum, raw, metadata, schema = row
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != checksum:
            raise ValueError("Raw snapshot checksum mismatch")
        return {
            "snapshot_id": snapshot_id, "market": market_key, "source": source,
            "received_at_utc": received, "payload_sha256": checksum,
            "raw_payload": json.loads(raw), "contract_metadata": json.loads(metadata),
            "schema_version": schema,
        }
