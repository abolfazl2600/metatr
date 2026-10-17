"""Atomic one-way external futures display protocol; Python remains source of truth."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path

import pandas as pd

from metatr.contract_economics import ContractSpec
from metatr.data import validate_bars
from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.model_registry import StrategySpec
from metatr.research import digest, parse_market
from metatr.strategies import StrategyDecision, utc

SCHEMA_VERSION = "1"
BAR_FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "product_id", "timeframe")
SIGNAL_FIELDS = (
    "bar_open_utc",
    "decision_utc",
    "action",
    "strategy_id",
    "strategy_version",
    "config_sha256",
    "reason",
    "data_sha256",
)
MANIFEST_FIELDS = (
    "schema_version",
    "market",
    "source",
    "capture_utc",
    "latest_bar_utc",
    "bar_count",
    "signal_count",
    "bars_file",
    "bars_bytes",
    "bars_sha256",
    "signals_file",
    "signals_bytes",
    "signals_sha256",
    "run_id",
    "data_sha256",
    "contract_sha256",
    "strategy_id",
    "strategy_version",
    "config_sha256",
    "status",
    "execution",
    "label",
)


def _csv(fields, rows):
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue().encode("ascii")


def _rows(data, fields):
    if len(data) > 32_000_000:
        raise ValueError("Display payload too large")
    reader = csv.DictReader(io.StringIO(data.decode("ascii")))
    if tuple(reader.fieldnames or ()) != tuple(fields):
        raise ValueError("Invalid display CSV schema")
    return list(reader)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def market_directory(output_dir: Path, market):
    parse_market(market.key)
    target = Path(output_dir) / "metatr" / digest(market.key)[:16]
    if any(p.is_symlink() for p in (target, target.parent)):
        raise ValueError("Display subtree cannot be a symlink")
    return target


def _atomic(path, data, *, immutable=False):
    if path.is_symlink():
        raise ValueError("Refuse display symlink")
    if immutable and path.exists():
        if path.read_bytes() != data:
            raise ValueError("Immutable display payload conflict")
        return
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".publish-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def publish(
    dataset,
    decisions: list[StrategyDecision],
    spec,
    contract: ContractSpec,
    output_dir: Path,
    *,
    now_utc,
    max_age_bars=3,
    retention=3,
) -> Path:
    dataset.verify()
    if (
        StrategySpec.create(
            spec.market,
            spec.strategy_id,
            spec.version,
            spec.implementation_key,
            spec.parameters,
            features=spec.features,
            warmup_bars=spec.warmup_bars,
            evaluation=spec.evaluation,
            created_at=spec.created_at,
            status=spec.status,
        )
        != spec
    ):
        raise ValueError("Invalid pinned display strategy")
    now = utc(now_utc)
    step = TIMEFRAME_SECONDS[dataset.market.timeframe]
    report = json.loads(dataset.validation_json)
    if not report or not report["ready"] or report["data_sha256"] != dataset.data_sha256:
        raise ValueError("Ready dataset required for display")
    checksum = report.pop("report_sha256")
    if (
        digest(report) != checksum
        or spec.market != dataset.market
        or contract.market != dataset.market
    ):
        raise ValueError("Display provenance/market mismatch")
    bars = dataset.bars
    latest = bars.index[-1]
    if (
        not 1 <= max_age_bars <= 100
        or not 2 <= retention <= 100
        or now < utc(dataset.asof_utc)
        or latest + pd.Timedelta(seconds=step) > now
        or (now - latest).total_seconds() > (max_age_bars + 1) * step
    ):
        raise ValueError("STALE, future or invalid display interval")
    calendar = report["calendar"]
    if (
        not calendar
        or calendar["calendar_id"] != contract.calendar_id
        or calendar["version"] != contract.calendar_version
        or spec.status == "retired"
    ):
        raise ValueError("Pinned active strategy and sourced calendar required")
    for item in dataset.provenance:
        contract.validate(item["contract_metadata"], now)
    status = "VALID"
    signals, seen = [], set()
    for decision in decisions:
        opened = utc(decision.bar_open_utc)
        if (
            decision.market != dataset.market.key
            or opened not in bars.index
            or opened in seen
            or utc(decision.timestamp) != opened + pd.Timedelta(seconds=step)
            or decision.strategy_id != spec.strategy_id
            or decision.strategy_version != spec.version
            or decision.config_sha256 != spec.config_sha256
            or decision.data_sha256 != dataset.data_sha256
            or tuple(decision.snapshot_ids) != dataset.snapshot_ids
            or decision.execution != "DISABLED"
        ):
            raise ValueError("Mixed or invalid display decision provenance")
        seen.add(opened)
        action = decision.action
        if action not in ("LONG", "SHORT", "NO_TRADE"):
            status, action = "INVALID", "NO_TRADE"
        reason = re.sub(r"[^a-zA-Z0-9 _.:;-]", "_", decision.reason)[:160]
        signals.append(
            dict(
                zip(
                    SIGNAL_FIELDS,
                    (
                        str(int(opened.timestamp())),
                        str(int(utc(decision.timestamp).timestamp())),
                        action,
                        spec.strategy_id,
                        spec.version,
                        spec.config_sha256,
                        reason,
                        dataset.data_sha256,
                    ),
                    strict=True,
                )
            )
        )
    if latest not in seen:
        status = "INVALID"
        signals.append(
            dict(
                zip(
                    SIGNAL_FIELDS,
                    (
                        str(int(latest.timestamp())),
                        str(int((latest + pd.Timedelta(seconds=step)).timestamp())),
                        "NO_TRADE",
                        spec.strategy_id,
                        spec.version,
                        spec.config_sha256,
                        "INVALID missing latest decision",
                        dataset.data_sha256,
                    ),
                    strict=True,
                )
            )
        )
    signals.sort(key=lambda row: int(row["bar_open_utc"]))
    records = [
        {
            "timestamp": str(int(t.timestamp())),
            **{c: str(row[c]) for c in row.index},
            "product_id": dataset.market.product_id,
            "timeframe": dataset.market.timeframe,
        }
        for t, row in bars.iterrows()
    ]
    bar_bytes, signal_bytes = _csv(BAR_FIELDS, records), _csv(SIGNAL_FIELDS, signals)
    source = ";".join(sorted({p["source"] for p in dataset.provenance}))
    if not re.fullmatch(r"[a-zA-Z0-9_.:;-]{1,160}", source):
        raise ValueError("Unsafe/unbounded display source")
    body = {
        "schema_version": SCHEMA_VERSION,
        "market": dataset.market.key,
        "source": source,
        "capture_utc": str(int(utc(dataset.asof_utc).timestamp())),
        "latest_bar_utc": str(int(latest.timestamp())),
        "bar_count": str(len(records)),
        "signal_count": str(len(signals)),
        "bars_sha256": _sha(bar_bytes),
        "signals_sha256": _sha(signal_bytes),
        "data_sha256": dataset.data_sha256,
        "contract_sha256": contract.fingerprint,
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.version,
        "config_sha256": spec.config_sha256,
        "status": status,
        "execution": "DISABLED",
        "label": "EXTERNAL_SIMULATED_DATA",
    }
    run_id = digest(body)
    body.update(
        run_id=run_id,
        bars_file=f"bars_{run_id}.csv",
        signals_file=f"signals_{run_id}.csv",
        bars_bytes=str(len(bar_bytes)),
        signals_bytes=str(len(signal_bytes)),
    )
    folder = market_directory(output_dir, dataset.market)
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / "writer.lock"
    lock.mkdir()  # one writer; crash leaves an explicit operator-review lock
    try:
        _atomic(folder / body["bars_file"], bar_bytes, immutable=True)
        _atomic(folder / body["signals_file"], signal_bytes, immutable=True)
        manifest = folder / "manifest.csv"
        _atomic(manifest, _csv(MANIFEST_FIELDS, [body]))  # commit marker LAST
        read_publication(folder, dataset.market, now_utc=now, max_age_bars=max_age_bars)
        # Only validated committed publication allows bounded old-generation cleanup.
        old = sorted(folder.glob("bars_*.csv"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in old[retention:]:
            if (
                re.fullmatch(r"bars_[0-9a-f]{64}\.csv", path.name)
                and path.name != body["bars_file"]
            ):
                old_id = path.stem[5:]
                path.unlink(missing_ok=True)
                (folder / f"signals_{old_id}.csv").unlink(missing_ok=True)
        return manifest
    finally:
        lock.rmdir()


def read_publication(folder: Path, market, *, now_utc, max_age_bars=3) -> dict:
    """Strict reference reader. On any failure keep prior UI visibly STALE/INVALID."""
    folder = Path(folder)
    rows = _rows((folder / "manifest.csv").read_bytes(), MANIFEST_FIELDS)
    if len(rows) != 1:
        raise ValueError("Manifest must have exactly one row")
    body = rows[0]
    if (
        body["schema_version"] != SCHEMA_VERSION
        or body["market"] != market.key
        or body["execution"] != "DISABLED"
        or body["status"] not in ("VALID", "INVALID")
        or body["label"] != "EXTERNAL_SIMULATED_DATA"
    ):
        raise ValueError("Invalid display identity/schema/status")
    parse_market(market.key)
    for name in ("run_id", "data_sha256", "contract_sha256", "config_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", body[name]):
            raise ValueError("Invalid display digest")
    core = {
        k: v
        for k, v in body.items()
        if k not in ("run_id", "bars_file", "signals_file", "bars_bytes", "signals_bytes")
    }
    if digest(core) != body["run_id"]:
        raise ValueError("Manifest run identity mismatch")
    payloads = {}
    for kind, fields in (("bars", BAR_FIELDS), ("signals", SIGNAL_FIELDS)):
        filename = body[f"{kind}_file"]
        if filename != f"{kind}_{body['run_id']}.csv" or (folder / filename).is_symlink():
            raise ValueError("Unsafe/mixed display payload filename")
        data = (folder / filename).read_bytes()
        if len(data) != int(body[f"{kind}_bytes"]) or _sha(data) != body[f"{kind}_sha256"]:
            raise ValueError("Display checksum/size mismatch")
        payloads[kind] = _rows(data, fields)
        count = body["bar_count" if kind == "bars" else "signal_count"]
        if len(payloads[kind]) != int(count) or not 1 <= int(count) <= 100_000:
            raise ValueError("Display row-count mismatch")
    records = payloads["bars"]
    if any(
        r["product_id"] != market.product_id or r["timeframe"] != market.timeframe for r in records
    ):
        raise ValueError("Mixed contract/timeframe in display bars")
    frame = pd.DataFrame(records).drop(columns=["product_id", "timeframe"])
    frame.index = pd.to_datetime(frame.pop("timestamp").astype("int64"), unit="s", utc=True)
    if not frame.index.is_monotonic_increasing:
        raise ValueError("Unordered display candles")
    checked = validate_bars(frame)
    step = TIMEFRAME_SECONDS[market.timeframe]
    now = utc(now_utc)
    latest = checked.index[-1]
    if (
        int(latest.timestamp()) != int(body["latest_bar_utc"])
        or latest + pd.Timedelta(seconds=step) > now
        or now.timestamp() - latest.timestamp() > (max_age_bars + 1) * step
        or int(body["capture_utc"]) > now.timestamp()
        or int(body["capture_utc"]) < latest.timestamp() + step
        or (checked.index.asi8 % (step * 10**9) != 0).any()
    ):
        raise ValueError("STALE/future/misaligned display source")
    seen = set()
    for signal in payloads["signals"]:
        opened = pd.to_datetime(int(signal["bar_open_utc"]), unit="s", utc=True)
        if (
            opened not in checked.index
            or opened in seen
            or int(signal["decision_utc"]) != int(opened.timestamp()) + step
            or signal["action"] not in ("LONG", "SHORT", "NO_TRADE")
            or any(
                signal[k] != body[k]
                for k in ("strategy_id", "strategy_version", "config_sha256", "data_sha256")
            )
        ):
            raise ValueError("Invalid display signal")
        seen.add(opened)
    if latest not in seen:
        raise ValueError("Missing current display decision")
    return {"manifest": body, **payloads}
