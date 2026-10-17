"""Stage-2 integration regressions; strictly synthetic, with no network or MT5 terminal."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_paper_runner import inputs
from test_paper_setup import capture

from metatr.mt5_publisher import market_directory, read_publication
from metatr.paper.runner import PaperRunner
from metatr.paper_display import publish_paper_display
from metatr.paper_observe import observe
from metatr.paper_setup import prepare, runner_args
from metatr.research import digest


def fixture_run(tmp_path):
    paper, dataset, spec, contract, costs, policy = inputs(tmp_path, 12)
    runner = PaperRunner(paper, "test")
    output = runner.process(dataset, spec, contract, costs, policy, "2")
    assert not output["halted"]
    decision = output.pop("last_decision")
    assert decision is not None
    return paper, dataset, spec, contract, costs, policy, runner, output, decision


def test_verified_paper_signal_publishes_one_nonexecuting_marker_and_evidence(tmp_path):
    paper, data, spec, contract, costs, policy, runner, result, decision = fixture_run(tmp_path)
    assert decision.data_sha256 != data.data_sha256
    handoff = publish_paper_display(
        data, decision, spec, contract, result, tmp_path / "common",
        now_utc=data.asof_utc, evidence_dir=tmp_path / "evidence",
    )
    assert handoff["status"] == "PUBLISHED_RESEARCH_SIGNAL_NOT_ORDER"
    payload = read_publication(market_directory(tmp_path / "common", data.market),
                               data.market, now_utc=data.asof_utc)
    assert payload["manifest"]["execution"] == "DISABLED"
    assert payload["manifest"]["label"] == "EXTERNAL_SIMULATED_DATA"
    assert payload["manifest"]["signal_count"] == "1"
    assert "PAPER_SIMULATED_" in payload["signals"][0]["reason"]
    assert payload["signals"][0]["action"] == decision.action
    evidence = json.loads(Path(handoff["evidence"]).read_text())
    assert evidence["run_id"] == digest({k: v for k, v in evidence.items()
                                         if k != "run_id"})
    assert evidence["original_prefix_sha256"] == decision.data_sha256
    assert evidence["display_dataset_sha256"] == data.data_sha256
    before = paper.inspect("test")
    repeat = runner.process(data, spec, contract, costs, policy, "2")
    assert repeat["processed_bars"] == 0 and repeat["last_decision"] is None
    assert paper.inspect("test") == before


@pytest.mark.parametrize("mutation", ["prefix", "model", "clock", "action", "halt"])
def test_wrong_provenance_model_checkpoint_risk_or_halt_cannot_publish(tmp_path, mutation):
    paper, data, spec, contract, costs, policy, runner, result, decision = fixture_run(tmp_path)
    if mutation == "prefix":
        decision = replace(decision, data_sha256="a" * 64)
    elif mutation == "model":
        decision = replace(decision, config_sha256="b" * 64)
    elif mutation == "clock":
        result["last_committed_candle_close_utc"] = data.start_utc
    elif mutation == "action":
        decision = replace(decision, action="SHORT" if decision.action != "SHORT" else "LONG")
    elif mutation == "halt":
        result["halted"] = True
    with pytest.raises(ValueError):
        publish_paper_display(data, decision, spec, contract, result, tmp_path / "common",
                              now_utc=data.asof_utc, evidence_dir=tmp_path / "evidence")
    assert not list((tmp_path / "common").rglob("manifest.csv"))


def test_public_setup_enables_automatic_observation_with_no_halt_reset(tmp_path):
    cap, _ = capture(tmp_path)
    root = tmp_path / "paper profile"
    prepare(root, feed=SimpleNamespace(capture_candles=lambda *a: cap))
    args = runner_args(root, "run", max_cycles=1)
    assert args[args.index("--observation-workspace") + 1] == str(root.resolve())
    from metatr.paper_cli import _record_observation, _verify_observation_workspace

    options = SimpleNamespace(
        observation_workspace=root, paper_db=root / "paper.sqlite",
        account="bip-paper", market="coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15",
    )
    _verify_observation_workspace(options)
    with pytest.raises(ValueError, match="mismatch"):
        _verify_observation_workspace(SimpleNamespace(
            observation_workspace=root, paper_db=tmp_path / "wrong.sqlite",
            account="bip-paper", market=options.market,
        ))
    result = {"processed_bars": 0, "halted": False}
    _record_observation(options, result, first=False)
    assert "observation" not in result
    _record_observation(options, result, first=True)
    assert "observation" in result and result["observation"]["reconciled"]
    assert "NO_COMMITTED_BAR" in result["observation"]["alerts"]
    evidence, observation = observe(root, now_utc="2020-01-02T00:05:00Z")
    assert evidence.is_file() and observation["reconciled"]
