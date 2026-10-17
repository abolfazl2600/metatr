"""Content-addressed offline signal replay, explicitly not a fill/PnL backtest."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from metatr.data import validate_bars
from metatr.exchanges.coinbase_futures import validate_contract_metadata
from metatr.futures_core import Market
from metatr.futures_store import OHLCV, SCHEMA_VERSION, TIMEFRAME_SECONDS, FuturesSQLiteStore
from metatr.model_registry import StrategySpec, _validate_market
from metatr.strategies import StrategyContext, predict, utc


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def parse_market(value: str) -> Market:
    parts = value.split(":")
    if len(parts) != 5:
        raise ValueError("Market must contain all five colon-separated identity fields")
    market = Market(*parts)
    _validate_market(market)
    if market.exchange != "coinbase" or market.asset != "BTC" or market.instrument != "FUTURE":
        raise ValueError("Only explicit Coinbase BTC FUTURE research is supported")
    if market.timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("Unknown timeframe")
    return market


@dataclass(frozen=True)
class ResearchDataset:
    market: Market
    bars_json: str
    provenance_json: str
    start_utc: str
    end_utc: str
    asof_utc: str
    data_sha256: str

    @property
    def bars(self) -> pd.DataFrame:
        records = json.loads(self.bars_json)
        frame = pd.DataFrame(records, columns=("timestamp", *OHLCV))
        frame.index = pd.to_datetime(frame.pop("timestamp"), utc=True)
        frame.index.name = None
        return validate_bars(frame)

    @property
    def provenance(self) -> list[dict]:
        return json.loads(self.provenance_json)

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        return tuple(item["snapshot_id"] for item in self.provenance)

    def verify(self) -> None:
        if digest({"market": self.market.key, "bars": json.loads(self.bars_json),
                   "provenance": self.provenance}) != self.data_sha256:
            raise ValueError("Dataset integrity failure")


def read_dataset(store: FuturesSQLiteStore, market: Market, start_utc, end_utc,
                 *, asof_utc=None) -> ResearchDataset:
    """Strict [start,end) candle-open interval. No partial selection or interpolation.

    Captured-as-of defaults to the latest pinned snapshot receipt time, never wall
    clock time. An explicit as-of requires all provenance to have been observed by it.
    Uses a single SQLite read snapshot and never calls a network connector.
    """
    parse_market(market.key)
    start, end = utc(start_utc), utc(end_utc)
    step = TIMEFRAME_SECONDS[market.timeframe]
    if start >= end or any(t.value % (step * 10**9) for t in (start, end)):
        raise ValueError("Invalid or unaligned [start,end) range")
    count = int((end - start).total_seconds() // step)
    if count > 100_000:
        raise ValueError("Research range exceeds 100000 bars")
    path = store.path.resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Unsupported market store schema")
        rows = db.execute(
            """SELECT candle_open_utc, open, high, low, close, volume, first_snapshot_id
               FROM verified_bars WHERE market_key=?
               AND candle_open_utc>=? AND candle_open_utc<? ORDER BY candle_open_utc""",
            (market.key, start.isoformat(), end.isoformat()),
        ).fetchall()
        if len(rows) != count:
            raise ValueError("Incomplete or empty candle range; no gap interpolation")
        snapshots = []
        for sid in sorted({row[-1] for row in rows}):
            raw = db.execute(
                """SELECT market_key,source,received_at_utc,payload_sha256,payload_json,
                   metadata_json,schema_version FROM raw_snapshots WHERE snapshot_id=?""",
                (sid,),
            ).fetchone()
            if raw is None:
                raise ValueError("Missing snapshot provenance")
            key, source, received, checksum, payload, metadata, schema = raw
            meta = json.loads(metadata)
            validate_contract_metadata(meta)
            identity = {"market": key, "source": source,
                        "payload_sha256": checksum, "metadata": metadata}
            if (key != market.key or not source.strip() or schema != SCHEMA_VERSION
                    or meta["product_id"] != market.product_id
                    or hashlib.sha256(payload.encode()).hexdigest() != checksum
                    or digest(identity) != sid):
                raise ValueError("Corrupt or mixed snapshot provenance")
            snapshots.append({"snapshot_id": sid, "source": source,
                              "received_at_utc": utc(received).isoformat(),
                              "payload_sha256": checksum, "contract_metadata": meta})
    observed = max(utc(p["received_at_utc"]) for p in snapshots)
    asof = utc(asof_utc) if asof_utc is not None else observed
    if observed > asof or end > asof:
        raise ValueError("Unclosed candles or provenance unavailable at as-of time")
    records = [list(row[:-1]) for row in rows]
    bars_json = canonical(records)
    frame = ResearchDataset(market, bars_json, canonical(snapshots), start.isoformat(),
                            end.isoformat(), asof.isoformat(), "").bars
    expected = pd.date_range(start, end, freq=pd.Timedelta(seconds=step), inclusive="left")
    if not frame.index.equals(expected):
        raise ValueError("Gapped, duplicated or misaligned candles")
    return ResearchDataset(market, bars_json, canonical(snapshots), start.isoformat(),
                           end.isoformat(), asof.isoformat(),
                           digest({"market": market.key, "bars": records,
                                   "provenance": snapshots}))


def code_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def replay(dataset: ResearchDataset, spec: StrategySpec, seed: int = 0) -> dict:
    dataset.verify()
    if spec.market != dataset.market or spec.status == "retired":
        raise ValueError("Unregistered market or retired strategy")
    bars = dataset.bars
    decisions = []
    step = pd.Timedelta(seconds=TIMEFRAME_SECONDS[dataset.market.timeframe])
    for pos, opened in enumerate(bars.index):
        ctx = StrategyContext(dataset.market, opened, opened + step, utc(dataset.asof_utc),
                              dataset.snapshot_ids, dataset.data_sha256, spec.warmup_bars, seed)
        decisions.append(predict(bars.iloc[:pos + 1], spec, ctx).to_dict())
    core = {"schema_version": 1, "market": dataset.market.key,
            "start_utc": dataset.start_utc, "end_utc_exclusive": dataset.end_utc,
            "earliest_bar_utc": bars.index[0].isoformat(),
            "latest_bar_utc": bars.index[-1].isoformat(), "bar_count": len(bars),
            "asof_utc": dataset.asof_utc, "data_sha256": dataset.data_sha256,
            "provenance": dataset.provenance, "strategy": spec.to_dict(), "seed": seed,
            "code_revision": code_revision(), "python_version": platform.python_version(),
            "package_versions": {name: importlib.metadata.version(name)
                                 for name in ("metatr-signal", "pandas", "numpy")},
            "research_only": True, "backtest_validated": False, "execution": "DISABLED",
            "action": "NO_TRADE", "reason": "signal replay alone is insufficient evidence",
            "decisions": decisions}
    return {"run_id": digest(core), **core}


def write_experiment(result: dict, output: Path) -> Path:
    """Atomically publish content-addressed bytes; never replace a different run."""
    run_id = result["run_id"]
    if digest({k: v for k, v in result.items() if k != "run_id"}) != run_id:
        raise ValueError("Experiment digest mismatch")
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{run_id}.json"
    content = (canonical(result) + "\n").encode()
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError("Immutable experiment conflict")
        return target
    fd, temp = tempfile.mkstemp(dir=output, prefix=".experiment-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # A hard link publishes complete bytes atomically without overwriting another writer.
        try:
            os.link(temp, target)
        except FileExistsError:
            if target.read_bytes() != content:
                raise ValueError("Immutable experiment conflict") from None
    finally:
        Path(temp).unlink(missing_ok=True)
    return target
