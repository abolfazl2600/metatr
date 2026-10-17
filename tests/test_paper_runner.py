import json
from dataclasses import asdict
from decimal import Decimal
from urllib.error import HTTPError

import pytest
from test_backtest import fixture

from metatr.paper.risk import PaperRiskPolicy
from metatr.paper.runner import PaperRunner, account_lock
from metatr.paper.store import PaperStore
from metatr.paper_cli import capture_public, main


def inputs(tmp_path, count=100):
    values = tuple(10000 + (i % 7) * 10 for i in range(count))
    dataset, spec, contract, costs, _ = fixture(tmp_path, values)
    paper = PaperStore(tmp_path / "paper.sqlite")
    paper.initialize("test", dataset.market, "1000", at_utc=dataset.start_utc)
    policy = PaperRiskPolicy(
        "test",
        dataset.market,
        "fixture",
        "1.0.0",
        "1000",
        "USD",
        "1000",
        "1000",
        "10",
        "1000",
        "1000",
        "1",
        1,
        1,
        0,
        "100",
        "0.2",
        2,
        "ASSUMED_FULLY_COLLATERALIZED",
        "synthetic paper fixture",
    )
    return paper, dataset, spec, contract, costs, policy


def test_100_bar_replay_twice_is_identical_and_never_double_debits(tmp_path):
    paper, dataset, spec, contract, costs, policy = inputs(tmp_path)
    runner = PaperRunner(paper, "test", audit_log=tmp_path / "audit.jsonl")
    result = runner.process(dataset, spec, contract, costs, policy, "2")
    assert result["processed_bars"] == 100 and not result["halted"]
    before = paper.inspect("test")
    repeated = runner.process(dataset, spec, contract, costs, policy, "2")
    assert repeated["processed_bars"] == 0
    assert paper.inspect("test") == before
    assert before["fills"] and paper.reconcile("test")
    assert all(
        json.loads(line)["label"] == "PAPER_ONLY"
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    )


class Crash(BaseException):
    pass


@pytest.mark.parametrize("stage", ["after_funding", "after_mark", "after_intent"])
def test_crash_boundary_resume_has_same_ledger(tmp_path, stage):
    paper, data, spec, contract, costs, policy = inputs(tmp_path, 12)
    calls = 0

    def crash(where, closed):
        nonlocal calls
        if where == stage:
            calls += 1
            if calls == 4:
                raise Crash()

    with pytest.raises(Crash):
        PaperRunner(paper, "test").process(
            data, spec, contract, costs, policy, "2", stage_hook=crash
        )
    assert paper.reconcile("test")
    resumed = PaperRunner(PaperStore(paper.path), "test").process(
        data, spec, contract, costs, policy, "2"
    )
    assert not resumed["halted"]
    path = tmp_path / "fresh"
    path.mkdir()
    reference, d, s, c, fees, p = inputs(path, 12)
    PaperRunner(reference, "test").process(d, s, c, fees, p, "2")
    assert paper.inspect("test")["ledger"] == reference.inspect("test")["ledger"]
    assert len(paper.inspect("test")["fills"]) == len(reference.inspect("test")["fills"])


def test_worker_lock_and_public_403_429_timeout(tmp_path):
    paper, data, _, _, _, _ = inputs(tmp_path, 12)
    with account_lock(paper.path, "test"):
        with pytest.raises(ValueError, match="Another paper worker"):
            with account_lock(paper.path, "test"):
                pass

    class Feed:
        def __init__(self, code):
            self.calls, self.code = 0, code

        def capture_candles(self, *args, **kwargs):
            self.calls += 1
            raise HTTPError("https://public.invalid", self.code, "fixture", {}, None)

    for code, expected in ((403, 1), (429, 3)):
        feed = Feed(code)
        with pytest.raises(ValueError, match=f"PUBLIC_HTTP_{code}"):
            capture_public(feed, data.market, None, None, wait=lambda _: None)
        assert feed.calls == expected


def test_cli_opt_in_offline_report_and_halt(tmp_path, monkeypatch):
    from metatr.model_registry import StrategyRegistry

    paper, data, spec, contract, costs, policy = inputs(tmp_path, 12)
    registry = StrategyRegistry(tmp_path / "registry.sqlite")
    registry.register(spec)
    bodies = {
        "contract": {**asdict(contract), "market": data.market.key},
        "costs": asdict(costs),
        "risk": {**asdict(policy), "market": data.market.key},
        "calendar": json.loads(data.validation_json)["calendar"],
    }
    bodies["calendar"]["market"] = data.market.key
    for key, body in bodies.items():
        (tmp_path / f"{key}.json").write_text(json.dumps(body))
    common = ["--paper-db", str(paper.path), "--account", "test", "--market", data.market.key]
    assert main(["status", *common, "--now-utc", data.end_utc]) == 0
    replay = [
        "replay",
        *common,
        "--mode",
        "paper",
        "--contracts",
        "2",
        "--store",
        str(tmp_path / "backtest.sqlite"),
        "--registry",
        str(registry.path),
        "--strategy-id",
        spec.strategy_id,
        "--strategy-version",
        spec.version,
        "--start-utc",
        data.start_utc,
        "--end-utc",
        data.end_utc,
        "--asof-utc",
        data.asof_utc,
    ]
    for flag, key in (
        ("contract-spec", "contract"),
        ("cost-scenario", "costs"),
        ("risk-policy", "risk"),
        ("calendar", "calendar"),
    ):
        replay.extend([f"--{flag}", str(tmp_path / f"{key}.json")])
    # Missing opt-in must stop before mutating the account or latching a kill switch.
    assert main(replay) == 1
    assert not paper.inspect("test")["halted"]
    assert main([*replay, "--confirm-paper"]) == 0
    assert (
        main(["report", *common, "--now-utc", data.end_utc, "--output", str(tmp_path / "reports")])
        == 0
    )
    assert main(["halt", *common, "--reason", "operator", "--now-utc", data.end_utc]) == 0
    assert PaperStore(paper.path).inspect("test")["halted"]
    assert Decimal(paper.inspect("test")["ledger"]["cash"]) < 1000
    public_common = list(common)
    public_common[3] = "public"
    assert (
        main(["init", *public_common, "--initial-equity", "1000", "--now-utc", data.start_utc]) == 0
    )
    bodies["risk"]["account"] = "public"
    (tmp_path / "risk.json").write_text(json.dumps(bodies["risk"]))
    calls = []

    def outage(*args, **kwargs):
        calls.append(True)
        raise ValueError("PUBLIC_HTTP_403")

    monkeypatch.setattr("metatr.paper_cli.capture_public", outage)
    public_run = ["run", *replay[1:], "--account", "public", "--confirm-paper", "--max-cycles", "1"]
    assert main(public_run) == 1 and len(calls) == 1
    assert paper.inspect("public")["halted"]
    assert not paper.inspect("public")["fills"]


def test_disk_full_catchup_limit_and_retirement_stop(tmp_path):
    from dataclasses import replace

    paper, data, spec, contract, costs, policy = inputs(tmp_path, 12)
    runner = PaperRunner(paper, "test")

    def fail(stage, closed):
        if stage == "after_funding":
            raise OSError("disk full fixture")

    with pytest.raises(OSError):
        runner.process(data, spec, contract, costs, policy, "2", stage_hook=fail)
    assert PaperStore(paper.path).inspect("test")["halted"]
    assert not paper.inspect("test")["fills"]
    assert paper.reconcile("test")
    for name, bad_spec, limit in [
        ("retired", replace(spec, status="retired"), 350),
        ("catchup", spec, 2),
    ]:
        path = tmp_path / name
        path.mkdir()
        other, d, _, c, fee, p = inputs(path, 12)
        with pytest.raises(ValueError):
            PaperRunner(other, "test").process(d, bad_spec, c, fee, p, "2", max_catch_up_bars=limit)
        assert other.inspect("test")["halted"]
        assert not other.inspect("test")["fills"]
