"""Offline historical readiness reports and bounded backfill plans."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from decimal import Decimal

import pandas as pd

from metatr.calendars import SessionCalendar, stamp
from metatr.exchanges.coinbase_futures import validate_contract_metadata
from metatr.futures_core import Market
from metatr.futures_store import OHLCV, TIMEFRAME_SECONDS, FuturesSQLiteStore
from metatr.research import ResearchDataset, canonical, digest, parse_market, read_dataset


@dataclass(frozen=True)
class DatasetValidationReport:
    market: str
    start_utc: str
    end_utc_exclusive: str
    asof_utc: str
    ready: bool
    issues: tuple[str, ...]
    missing_windows: tuple[tuple[str, str], ...]
    invalid_windows: tuple[tuple[str, str], ...]
    calendar_json: str
    data_sha256: str | None
    snapshot_ids: tuple[str, ...]
    schema_version: int = 1
    execution: str = "DISABLED"

    def to_dict(self) -> dict:
        result = asdict(self)
        result["calendar"] = json.loads(result.pop("calendar_json"))
        result["report_sha256"] = digest(result)
        return result

    @property
    def calendar_version(self) -> str | None:
        calendar = json.loads(self.calendar_json)
        return calendar["version"] if calendar else None


def _windows(missing: pd.DatetimeIndex, step: int) -> tuple[tuple[str, str], ...]:
    windows = []
    for opened in missing:
        end = opened + pd.Timedelta(seconds=step)
        if windows and stamp(windows[-1][1]) == opened:
            windows[-1] = (windows[-1][0], end.isoformat())
        else:
            windows.append((opened.isoformat(), end.isoformat()))
    return tuple(windows)


def _audit_snapshots(store, market, start, end, asof, bars):
    """Compare every overlapping raw response, including non-first captures."""
    issues = set()
    evidence = set()
    metadata_fingerprints = set()
    known = {t: tuple(Decimal(str(row[c])) for c in OHLCV) for t, row in bars.iterrows()}
    with sqlite3.connect(store.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("""SELECT snapshot_id,source,received_at_utc,payload_sha256,
            payload_json,metadata_json FROM raw_snapshots WHERE market_key=?""",
                          (market.key,)).fetchall()
    for sid, source, received, checksum, raw, metadata in rows:
        try:
            if stamp(received) > asof:
                continue
            payload = json.loads(raw)
            candles = payload.get("candles")
            if not isinstance(candles, list):
                # A non-candle snapshot cannot prove candle provenance.
                continue
            matching = [r for r in candles if start <= pd.to_datetime(
                int(r["start"]), unit="s", utc=True) < end]
            if not matching:
                continue
            meta = json.loads(metadata)
            validate_contract_metadata(meta)
            if (not source.strip() or meta["product_id"] != market.product_id
                    or hashlib.sha256(raw.encode()).hexdigest() != checksum
                    or digest({"market": market.key, "source": source,
                               "payload_sha256": checksum, "metadata": metadata}) != sid):
                raise ValueError("Corrupt source")
            details = meta["future_product_details"]
            metadata_fingerprints.add(canonical({k: details.get(k) for k in
                                                ("contract_code", "contract_size",
                                                 "contract_root_unit", "contract_expiry")}))
            seen = set()
            for row in matching:
                opened = pd.to_datetime(int(row["start"]), unit="s", utc=True)
                values = tuple(Decimal(str(row[c])) for c in OHLCV)
                if opened in seen:
                    issues.add("DUPLICATE_SOURCE_TIMESTAMP")
                seen.add(opened)
                if not all(v.is_finite() for v in values) or known.get(opened) != values:
                    issues.add("SOURCE_DISAGREEMENT")
                else:
                    evidence.add(opened)
        except (ValueError, TypeError, KeyError, ArithmeticError, OverflowError):
            issues.add("INVALID_SOURCE_PROVENANCE")
    if set(known) - evidence:
        issues.add("MISSING_RAW_CANDLE_PROVENANCE")
    if len(metadata_fingerprints) > 1:
        issues.add("CONTRACT_METADATA_CONFLICT")
    return issues


def validate_dataset(store: FuturesSQLiteStore, market: Market, start_utc, end_utc,
                     asof_utc, *, calendar: SessionCalendar | None = None,
                     require_fresh: bool = False, max_age_bars: int = 2
                     ) -> tuple[DatasetValidationReport, ResearchDataset | None]:
    parse_market(market.key)
    start, end, asof = map(stamp, (start_utc, end_utc, asof_utc))
    step = TIMEFRAME_SECONDS[market.timeframe]
    if (start >= end or end > asof or max_age_bars < 0
            or any(t.value % (step * 10**9) for t in (start, end))
            or (end - start).total_seconds() / step > 100_000):
        raise ValueError("Invalid quality-gate interval/asof")
    expected = (calendar.expected(market, start, end) if calendar else pd.date_range(
        start, end, freq=pd.Timedelta(seconds=step), inclusive="left"))
    issues = set()
    dataset = None
    try:
        bars = store.read_bars(market, start_utc=start, end_utc=end, asof_utc=asof)
        missing = expected.difference(bars.index)
        extra = bars.index.difference(expected)
        if len(extra):
            issues.add("SESSION_OR_ALIGNMENT_VIOLATION")
        issues.update(_audit_snapshots(store, market, start, end, asof, bars))
        if require_fresh:
            fresh_end = asof.floor(pd.Timedelta(seconds=step))
            if calendar:
                newer = calendar.expected(market, end, fresh_end) if end < fresh_end else []
                age = len(newer)
            else:
                age = (fresh_end - end).total_seconds() / step
            if age > max_age_bars:
                issues.add("STALE_LAST_CLOSED_BAR")
        if not len(missing) and not issues:
            dataset = read_dataset(store, market, start, end, asof_utc=asof, calendar=calendar)
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        if "No stored candles" in str(exc):
            missing = expected
            extra = pd.DatetimeIndex([], tz="UTC")
            issues.add("EMPTY_DATASET")
        else:
            missing = expected
            extra = pd.DatetimeIndex([], tz="UTC")
            issues.add("INVALID_DATA_OR_PROVENANCE")
    if len(missing):
        issues.add("MISSING_CANDLES" if calendar else "UNVERIFIED_GAP")
    report = DatasetValidationReport(
        market.key, start.isoformat(), end.isoformat(), asof.isoformat(),
        not issues and dataset is not None, tuple(sorted(issues)), _windows(missing, step),
        _windows(extra, step), canonical(asdict(calendar)) if calendar else "null",
        dataset.data_sha256 if dataset else None, dataset.snapshot_ids if dataset else (),
    )
    if dataset is not None:
        dataset = replace(dataset, validation_json=canonical(report.to_dict()))
    return report, dataset
