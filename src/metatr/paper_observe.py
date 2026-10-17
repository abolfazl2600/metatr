"""Read-only, reproducible local PAPER health evidence and optional SQLite backups.

This is a point-in-time observation, NOT a background worker or a 14-day sign-off.
No market requests, account mutations, order APIs, or automatic halt resets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from metatr.calendars import stamp
from metatr.paper.runner import status
from metatr.paper.store import PaperStore
from metatr.research import canonical, digest, parse_market, write_experiment


def _snapshot(source: Path, destination: Path) -> str:
    """Use SQLite's online backup API so WAL-backed sources are copied consistently."""
    if not source.is_file() or source.is_symlink():
        raise ValueError("Required SQLite source is missing or a symlink")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True,
                                 timeout=5)) as reader:
        with closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer, pages=1000, sleep=0.1)
            result = writer.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise ValueError("SQLite backup integrity check failed")
    with destination.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
    return sha


def _retain_backup(source: Path, destination: Path, sha: str) -> Path:
    """Publish the fully closed temporary backup without replacing older evidence."""
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("Refuse symlink backup directory")
    target = destination / f"{source.stem}-{sha}.sqlite"
    if target.exists():
        with target.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != sha:
                raise ValueError("Immutable SQLite backup conflict")
    else:
        os.link(source, target)
    return target


def _seconds(now, then):
    return (stamp(now) - stamp(then)).total_seconds()


def observe(workspace, *, output_dir=None, now_utc=None, backup=False, max_stale_seconds=2700):
    """Capture and store an immutable evidence file; observe, never run or reset PAPER."""
    if max_stale_seconds < 900:
        raise ValueError("Freshness threshold must be at least one M15 bar (900 seconds)")
    workspace = Path(workspace).expanduser().resolve()
    profile_path = workspace / "setup.json"
    if not profile_path.is_file() or profile_path.is_symlink():
        raise ValueError("Missing or unsafe paper setup profile")
    profile = json.loads(profile_path.read_text())
    if (profile.get("schema_version") != 1 or profile.get("label") != "PAPER_ONLY_SIMULATED"
            or not isinstance(profile.get("account"), str)
            or not profile["account"] or not isinstance(profile.get("market"), str)):
        raise ValueError("Unknown paper setup profile")
    parse_market(profile["market"])
    observed = stamp(now_utc or datetime.now(timezone.utc).isoformat())
    output = Path(output_dir).expanduser().resolve() if output_dir else workspace / "observations"
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("Refuse symlink observation directory")

    backups = {}
    warnings = []
    summary = None
    journal_head = None
    journal_events = None
    error_type = None

    with tempfile.TemporaryDirectory(prefix=".paper-observe-", dir=output) as temporary:
        staging = Path(temporary)
        os.chmod(staging, 0o700)
        paper_file = staging / "paper.sqlite"
        try:
            paper_sha = _snapshot(workspace / "paper.sqlite", paper_file)
            if backup:
                saved = _retain_backup(paper_file, output / "backups", paper_sha)
                backups["paper"] = {"sha256": paper_sha, "path": str(saved)}
            paper = PaperStore(paper_file)
            paper.reconcile(profile["account"])
            summary = status(paper, profile["account"], observed)
            if summary["market"] != profile["market"]:
                raise ValueError("Profile/account market mismatch")
            with paper.connection() as db:
                row = db.execute(
                    "SELECT event_hash FROM audit_events WHERE account=? "
                    "ORDER BY sequence DESC LIMIT 1", (profile["account"],)
                ).fetchone()
                journal_head = row[0] if row else None
                journal_events = db.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE account=?", (profile["account"],)
                ).fetchone()[0]
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError) as exc:
            # An observation MUST record a failed reconciliation as unsafe,
            # never claim a healthy state from incomplete reads.
            error_type = type(exc).__name__
            warnings.append("PAPER_RECONCILIATION_OR_READ_FAILURE")
        if backup:
            for name in ("market", "registry"):
                filename = workspace / f"{name}.sqlite"
                try:
                    target = staging / f"{name}.sqlite"
                    sha = _snapshot(filename, target)
                    saved = _retain_backup(target, output / "backups", sha)
                    backups[name] = {"sha256": sha, "path": str(saved)}
                except (ValueError, sqlite3.Error, OSError):
                    warnings.append(f"{name.upper()}_BACKUP_FAILED")

    if summary is not None:
        if summary["halted"]:
            warnings.append("PAPER_HALTED")
        checkpoint = summary["last_committed_candle_close_utc"]
        if checkpoint is None:
            warnings.append("NO_COMMITTED_BAR")
        else:
            age = _seconds(observed, checkpoint)
            if age < -60:
                warnings.append("CHECKPOINT_IN_FUTURE")
            if age > max_stale_seconds:
                warnings.append("BAR_STALE")
        source = summary["source_received_utc"]
        if source is None:
            warnings.append("NO_SOURCE_RECEIPT")
        else:
            source_age = _seconds(observed, source)
            if source_age < -60:
                warnings.append("SOURCE_RECEIPT_IN_FUTURE")
            if source_age > max_stale_seconds:
                warnings.append("SOURCE_STALE")
    coverage = profile.get("session_coverage_end_utc")
    if not coverage:
        warnings.append("SESSION_COVERAGE_UNKNOWN")
    elif _seconds(observed, coverage) >= 0:
        warnings.append("SESSION_COVERAGE_ENDED")
    if backup and len(backups) != 3:
        warnings.append("BACKUP_SET_INCOMPLETE")
    warnings = sorted(set(warnings))
    result = {
        "schema_version": 1,
        "label": "PAPER_ONLY_SIMULATED",
        "kind": "OPERATOR_OBSERVATION",
        "observed_utc": observed.isoformat(),
        "clock_source": "OPERATOR_OVERRIDE" if now_utc else "SYSTEM_UTC",
        "account": profile["account"],
        "market": profile["market"],
        "profile_sha256": digest(profile),
        "health": "ATTENTION" if warnings else "OK",
        "alerts": warnings,
        "reconciled": summary is not None,
        "read_failure_type": error_type,
        "paper_journal_head": journal_head,
        "paper_journal_events": journal_events,
        "paper_status": summary,
        "backups": backups,
        "sqlite_backup_scope": "EACH_DATABASE_CONSISTENT_NOT_CROSS_DATABASE_ATOMIC",
        "operational_signoff": "PENDING_MONITORED_OBSERVATION",
        "native_mt5_signoff": "PENDING_MANUAL_EVIDENCE",
        "real_execution_available": False,
    }
    evidence = write_experiment({"run_id": digest(result), **result}, output)
    return evidence, result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Observe PAPER ONLY; no worker or trading")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--backup", action="store_true", help="Keep SQLite online backups")
    parser.add_argument("--max-stale-seconds", type=int, default=2700)
    parser.add_argument("--require-healthy", action="store_true",
                        help="Return nonzero for health alerts (never auto-reset HALT)")
    parser.add_argument("--now-utc", help="Explicit audit clock override, never native sign-off")
    args = parser.parse_args(argv)
    try:
        path, result = observe(
            args.workspace, output_dir=args.output_dir, now_utc=args.now_utc,
            backup=args.backup, max_stale_seconds=args.max_stale_seconds,
        )
        print(canonical({"evidence": str(path), "health": result["health"],
                         "alerts": result["alerts"], "label": result["label"]}))
        return 2 if args.require_healthy and result["alerts"] else 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"PAPER observation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
