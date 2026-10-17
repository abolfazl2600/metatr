"""Offline safety checks for point-in-time PAPER evidence; no public API needed."""

import json

import pytest
from test_paper_setup import capture

from metatr.paper.store import PaperStore
from metatr.paper_observe import main, observe
from metatr.paper_setup import prepare


def prepared(tmp_path):
    cap, _ = capture(tmp_path)
    root = tmp_path / "observed account"
    prepare(root, feed=type("Feed", (), {"capture_candles": lambda self, *a: cap})())
    return root


def test_observe_does_not_mutate_account_and_preserves_sqlite_backups(tmp_path):
    root = prepared(tmp_path)
    paper = PaperStore(root / "paper.sqlite")
    before = paper.inspect("bip-paper")
    evidence, result = observe(root, now_utc="2020-01-02T00:05:00Z", backup=True)
    saved = json.loads(evidence.read_text())
    assert result["clock_source"] == "OPERATOR_OVERRIDE"
    assert saved["run_id"] == evidence.stem
    assert saved["kind"] == "OPERATOR_OBSERVATION"
    assert saved["health"] == "ATTENTION"
    assert "NO_COMMITTED_BAR" in result["alerts"]
    assert "NO_SOURCE_RECEIPT" in result["alerts"]
    assert saved["reconciled"]
    assert set(saved["backups"]) == {"paper", "market", "registry"}
    assert all((root / "observations" / "backups").joinpath(
        name + "-" + saved["backups"][name]["sha256"] + ".sqlite"
    ).is_file() for name in ("paper", "market", "registry"))
    assert PaperStore(root / "paper.sqlite").inspect("bip-paper") == before
    assert observe(root, now_utc="2020-01-02T00:05:00Z", backup=True)[0].is_file()


def test_halt_remains_set_and_an_unhealthy_observation_fails_a_health_gate(tmp_path):
    root = prepared(tmp_path)
    paper = PaperStore(root / "paper.sqlite")
    paper.halt("bip-paper", "manual halt for review", at_utc="2020-01-02T00:00:00Z")
    evidence, result = observe(root, now_utc="2020-01-03T00:30:00Z")
    assert evidence.is_file()
    assert "PAPER_HALTED" in result["alerts"]
    assert "SESSION_COVERAGE_ENDED" in result["alerts"]
    assert PaperStore(root / "paper.sqlite").inspect("bip-paper")["halted"]
    assert main(["--workspace", str(root), "--now-utc", "2020-01-03T00:30:00Z",
                 "--require-healthy"]) == 2


def test_unreadable_paper_is_recorded_as_an_alert_not_a_pass(tmp_path):
    root = prepared(tmp_path)
    (root / "paper.sqlite").write_bytes(b"not a sqlite database")
    evidence, result = observe(root, now_utc="2020-01-02T00:05:00Z")
    assert evidence.is_file()
    assert not result["reconciled"]
    assert "PAPER_RECONCILIATION_OR_READ_FAILURE" in result["alerts"]
    assert result["health"] == "ATTENTION"


def test_invalid_profile_or_unreasonably_short_freshness_refused(tmp_path):
    root = prepared(tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        observe(root, max_stale_seconds=50)
    with pytest.raises(ValueError, match="profile"):
        observe(tmp_path / "nonexistent")
    (root / "setup.json").write_text('{"schema_version": 999}')
    with pytest.raises(ValueError, match="profile"):
        observe(root)
