import json
import socket

import pytest

from metatr.pipeline_cli import main, run_pipeline
from metatr.research import parse_market
from metatr.strategies import utc

MARKET = parse_market("coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15")
NOW = "2020-01-02T00:00:00Z"


def test_full_offline_handoff_pins_and_repeat_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network"))
    args = (MARKET, tmp_path / "state", tmp_path / "common", NOW)
    manifest, evidence = run_pipeline(*args)
    before = manifest.read_bytes(), evidence.read_bytes()
    assert run_pipeline(*args) == (manifest, evidence)
    assert before == (manifest.read_bytes(), evidence.read_bytes())
    report = json.loads(evidence.read_text())
    assert report["manifest"]["run_id"] == (
        "48e984806a8ed4f2f84607906367bed407402c3d3fa921dbf7e34749bc96e4b2")
    assert report["manifest"]["bar_count"] == report["manifest"]["signal_count"] == "80"
    assert set(report["strategies"]) == {"ema_cross", "momentum"}
    assert report["native_mt5_acceptance"] == "PENDING_MANUAL_EVIDENCE"
    _, next_evidence = run_pipeline(*args[:3], "2020-01-02T00:15:00Z")
    assert json.loads(next_evidence.read_text())["manifest"]["run_id"] != (
        report["manifest"]["run_id"])
    assert evidence.read_bytes() == before[1]


def test_ema_handoff_and_cli_mode_guards(tmp_path):
    _, evidence = run_pipeline(MARKET, tmp_path / "state", tmp_path / "common", NOW,
                               strategy_id="ema_cross")
    assert json.loads(evidence.read_text())["manifest"]["strategy_id"] == "ema_cross"
    args = ["--workspace", str(tmp_path / "cli"), "--output-dir", str(tmp_path / "out")]
    assert main(["--live", *args]) == 1  # No synthetic fallback or public request without pins.
    assert not (tmp_path / "out").exists()
    assert main(["--live", "--now-utc", NOW, *args]) == 1
    assert main(["--fixture", "--now-utc", NOW, *args]) == 0


def test_opt_in_public_capture_uses_same_handoff_and_never_falls_back(tmp_path, monkeypatch):
    from dataclasses import asdict
    from types import SimpleNamespace

    from metatr.display_fixture import fixture_dataset
    from metatr.pipeline_cli import CoinbaseFuturesFeed

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    dataset, _, contract = fixture_dataset(MARKET, NOW, fixture)
    contract_json = asdict(contract)
    contract_json["market"] = MARKET.key
    calendar_json = json.loads(dataset.validation_json)["calendar"]
    calendar_json["market"] = MARKET.key
    contract_path, calendar_path = tmp_path / "contract.json", tmp_path / "calendar.json"
    contract_path.write_text(json.dumps(contract_json))
    calendar_path.write_text(json.dumps(calendar_json))
    candles = [{"start": str(int(t.timestamp())), **{c: str(row[c]) for c in row.index}}
               for t, row in dataset.bars.iterrows()]
    capture = SimpleNamespace(bars=dataset.bars, raw_payload={"candles": candles},
                              product=dataset.provenance[0]["contract_metadata"],
                              captured_at=utc(NOW).to_pydatetime())
    monkeypatch.setattr(CoinbaseFuturesFeed, "capture_candles", lambda *a: capture)
    _, evidence = run_pipeline(MARKET, tmp_path / "live", tmp_path / "common", NOW,
                               live=True, contract_path=contract_path, calendar_path=calendar_path)
    report = json.loads(evidence.read_text())
    assert report["mode"] == "PUBLIC_LIVE"
    assert report["manifest"]["source"] == "coinbase_public_futures"
    assert report["execution"] == "DISABLED"

    def unavailable(*args):
        raise OSError("public source inaccessible")

    monkeypatch.setattr(CoinbaseFuturesFeed, "capture_candles", unavailable)
    with pytest.raises(OSError, match="inaccessible"):
        run_pipeline(MARKET, tmp_path / "failed", tmp_path / "absent", NOW,
                     live=True, contract_path=contract_path, calendar_path=calendar_path)
    assert not (tmp_path / "absent").exists()
