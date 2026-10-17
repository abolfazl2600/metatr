"""Summarize real-clock PAPER evidence, without inferring uptime or approval."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from metatr.calendars import stamp
from metatr.research import canonical, digest, write_experiment


def summarize(workspace, *, output_dir=None, max_gap_seconds=1800):
    """Only separately captured SYSTEM_UTC observations count as monitoring evidence."""
    if max_gap_seconds < 900:
        raise ValueError("Gap threshold must be >= one 15-minute candle")
    workspace = Path(workspace).expanduser().resolve()
    setup = json.loads((workspace / "setup.json").read_text())
    if setup.get("schema_version") != 1 or setup.get("label") != "PAPER_ONLY_SIMULATED":
        raise ValueError("Unrecognized Paper setup profile")
    account, market = setup["account"], setup["market"]
    readings = []
    overrides = 0
    for path in sorted((workspace / "observations").glob("*.json")):
        if path.is_symlink():
            raise ValueError("Refuse symlink observation evidence")
        body = json.loads(path.read_text())
        run_id = body.pop("run_id", None)
        if (run_id != path.stem or run_id != digest(body)
                or body.get("kind") != "OPERATOR_OBSERVATION"
                or body.get("account") != account or body.get("market") != market):
            raise ValueError("Observation is corrupt or does not belong to this account")
        clock = body["clock_source"]
        if clock == "OPERATOR_OVERRIDE":
            overrides += 1
            continue
        if clock != "SYSTEM_UTC":
            raise ValueError("Unknown observation clock source")
        time = stamp(body["observed_utc"])
        if time > stamp(datetime.now(timezone.utc)) + timedelta(minutes=2):
            raise ValueError("Future-dated observation cannot count as monitoring")
        readings.append((time, body))
    readings.sort(key=lambda row: row[0])
    by_day = defaultdict(lambda: {"observations": 0, "healthy": 0, "alerts": 0,
                                  "halted_observations": 0})
    alerts = Counter()
    gaps = []
    for index, (time, body) in enumerate(readings):
        day = by_day[time.date().isoformat()]
        day["observations"] += 1
        if body["health"] == "OK" and body["reconciled"] and not body["alerts"]:
            day["healthy"] += 1
        else:
            day["alerts"] += 1
        if body.get("paper_status") and body["paper_status"].get("halted"):
            day["halted_observations"] += 1
        alerts.update(body["alerts"])
        if index:
            seconds = (time - readings[index - 1][0]).total_seconds()
            if seconds > max_gap_seconds:
                gaps.append({"from_utc": readings[index - 1][0].isoformat(),
                             "to_utc": time.isoformat(), "gap_seconds": seconds})
    latest = readings[-1][1] if readings else None
    result = {
        "schema_version": 1,
        "kind": "PAPER_MONITORING_SUMMARY",
        "label": "PAPER_ONLY_SIMULATED",
        "account": account,
        "market": market,
        "profile_sha256": digest(setup),
        "real_clock_observations": len(readings),
        "operator_clock_overrides_excluded": overrides,
        "observed_days": len(by_day),
        "first_observed_utc": readings[0][0].isoformat() if readings else None,
        "last_observed_utc": readings[-1][0].isoformat() if readings else None,
        "daily_observation_counts": dict(sorted(by_day.items())),
        "alert_counts": dict(sorted(alerts.items())),
        "gaps_over_threshold": gaps,
        "last_observed_paper_status": latest["paper_status"] if latest else None,
        "last_observation_health": latest["health"] if latest else "NO_REAL_CLOCK_EVIDENCE",
        "monitoring_scope": "SAMPLED_LOCAL_OBSERVATIONS_NOT_CONTINUOUS_UPTIME",
        "operational_signoff": "PENDING_OPERATOR_REVIEW",
        "native_mt5_signoff": "PENDING_MANUAL_EVIDENCE",
        "real_execution_available": False,
    }
    output = (Path(output_dir).expanduser().resolve() if output_dir
              else workspace / "monitoring-reports")
    target = write_experiment({"run_id": digest(result), **result}, output)
    return target, result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Aggregate immutable PAPER observations")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-gap-seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        path, result = summarize(args.workspace, output_dir=args.output_dir,
                                 max_gap_seconds=args.max_gap_seconds)
        print(canonical({"report": str(path), "observed_days": result["observed_days"],
                         "real_clock_observations": result["real_clock_observations"],
                         "alert_counts": result["alert_counts"],
                         "operational_signoff": result["operational_signoff"]}))
        return 0
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"PAPER monitoring report stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
