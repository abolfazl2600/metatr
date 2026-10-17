"""Monitoring summary never confuses offline timestamps with real observations."""

import json
from types import SimpleNamespace

import pytest
from test_paper_setup import capture

from metatr.paper_monitor_report import main, summarize
from metatr.paper_observe import observe
from metatr.paper_setup import prepare


def setup(tmp_path):
    cap, _ = capture(tmp_path)
    root = tmp_path / "monitor"
    prepare(root, feed=SimpleNamespace(capture_candles=lambda *args: cap))
    return root


def test_real_clock_observations_aggregate_without_implying_uptime(tmp_path):
    root = setup(tmp_path)
    _, empty = summarize(root)
    assert empty["observed_days"] == 0
    assert empty["last_observation_health"] == "NO_REAL_CLOCK_EVIDENCE"
    observe(root)  # genuine system clock; fixture account is intentionally stale
    observe(root, now_utc="2020-01-02T00:05:00Z")
    target, report = summarize(root)
    assert target.is_file()
    assert report["real_clock_observations"] == 1
    assert report["operator_clock_overrides_excluded"] == 1
    assert report["observed_days"] == 1
    assert report["operational_signoff"] == "PENDING_OPERATOR_REVIEW"
    assert report["last_observation_health"] == "ATTENTION"
    assert "SESSION_COVERAGE_ENDED" in report["alert_counts"]
    assert main(["--workspace", str(root)]) == 0


def test_immutable_observation_corruption_is_not_ignored(tmp_path):
    root = setup(tmp_path)
    path, _ = observe(root, now_utc="2020-01-02T00:05:00Z")
    record = json.loads(path.read_text())
    record["health"] = "OK"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="corrupt"):
        summarize(root)
    assert main(["--workspace", str(root)]) == 1


def test_observation_symlink_is_rejected(tmp_path):
    root = setup(tmp_path)
    other = tmp_path / "other.json"
    other.write_text("{}")
    (root / "observations").mkdir()
    (root / "observations" / "bogus.json").symlink_to(other)
    with pytest.raises(ValueError, match="symlink"):
        summarize(root)
