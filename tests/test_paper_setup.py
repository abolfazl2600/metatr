import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pandas as pd
import pytest

from metatr.contract_economics import native_contract_fingerprint
from metatr.display_fixture import fixture_dataset
from metatr.paper.runner import WorkerAlreadyRunning, account_lock
from metatr.paper.store import PaperStore
from metatr.paper_setup import (
    MARKET,
    _capture_with_retry,
    _checkpoint_continuity,
    main,
    prepare,
    renew_session,
    reset_halt,
    runner_args,
)


def capture(tmp_path):
    fixture = tmp_path / "seed"
    fixture.mkdir()
    dataset, _, contract = fixture_dataset(MARKET, "2020-01-02T00:00:00Z", fixture)
    meta = copy.deepcopy(dataset.provenance[0]["contract_metadata"])
    meta.update(base_min_size="1", base_increment="1", price_increment="5")
    meta["fcm_trading_session_details"] = {
        "is_session_open": True, "session_state": "FCM_TRADING_SESSION_STATE_OPEN",
        "open_time": "2020-01-01T00:00:00Z", "close_time": "2020-01-03T00:00:00Z"}
    candles = [{"start": str(int(t.timestamp())), **{c: str(row[c]) for c in row.index}}
               for t, row in dataset.bars.iterrows()]
    return SimpleNamespace(product=meta, bars=dataset.bars, raw_payload={"candles": candles},
                           captured_at=pd.Timestamp(dataset.asof_utc)), contract


def test_structural_pin_accepts_quote_changes_but_rejects_spec_changes(tmp_path):
    cap, contract = capture(tmp_path)
    spec = replace(contract, tick_size="5",
                   metadata_sha256=native_contract_fingerprint(cap.product))
    changed = copy.deepcopy(cap.product)
    changed.update(price="90000", volume_24h="12345")
    changed["future_product_details"]["funding_rate"] = "0.01"
    changed["fcm_trading_session_details"]["is_session_open"] = False
    spec.validate(changed, cap.captured_at)
    changed["price_increment"] = "10"
    with pytest.raises(ValueError):
        spec.validate(changed, cap.captured_at)
    changed = copy.deepcopy(cap.product)
    changed["future_product_details"]["contract_size"] = "0.02"
    with pytest.raises(ValueError):
        spec.validate(changed, cap.captured_at)


def test_prepare_atomic_account_and_explicit_start(tmp_path):
    cap, _ = capture(tmp_path)
    feed = SimpleNamespace(capture_candles=lambda *a: cap)
    root = tmp_path / "profile with spaces"
    profile = prepare(root, feed=feed)
    assert profile["label"] == "PAPER_ONLY_SIMULATED"
    assert profile["session_coverage_end_utc"] == "2020-01-03T00:00:00+00:00"
    assert (root / "paper.sqlite").is_file()
    args = runner_args(root, "run", max_cycles=2)
    assert args[0] == "run" and "--confirm-paper" in args
    assert str(root / "paper.sqlite") in args
    with pytest.raises(ValueError, match="already exists"):
        prepare(root, feed=feed)
    assert main(["--workspace", str(root), "--start"]) == 1
    assert main(["--workspace", str(root), "--status"]) == 0


def test_closed_or_uncovered_session_never_creates_account(tmp_path):
    cap, _ = capture(tmp_path)
    cap.product["fcm_trading_session_details"]["is_session_open"] = False
    feed = SimpleNamespace(capture_candles=lambda *a: cap)
    root = tmp_path / "invalid"
    with pytest.raises(ValueError, match="open session"):
        prepare(root, feed=feed)
    assert not root.exists()


def test_public_poll_rechecks_current_metadata_before_keeping_old_candles(tmp_path):
    from metatr.paper_cli import capture_public

    cap, contract = capture(tmp_path)
    contract = replace(contract, tick_size="5",
                       metadata_sha256=native_contract_fingerprint(cap.product))
    feed = SimpleNamespace(capture_candles=lambda *a, **k: cap)
    # The immutable old candles cannot conceal a new contract tick on an idle poll.
    cap.product["price_increment"] = "10"
    with pytest.raises(ValueError):
        capture_public(feed, MARKET, None, None, contract=contract)
    cap.product["price_increment"] = "5"
    cap.product["fcm_trading_session_details"]["is_session_open"] = False
    with pytest.raises(ValueError, match="PUBLIC_SESSION_CLOSED"):
        capture_public(feed, MARKET, None, None, contract=contract)


def test_initial_public_capture_recovers_after_transient_connection_reset(tmp_path):
    cap, _ = capture(tmp_path)
    calls = []
    delays = []

    def fetch(*args):
        calls.append(args)
        if len(calls) < 3:
            raise URLError(ConnectionResetError(54, "Connection reset by peer"))
        return cap

    feed = SimpleNamespace(capture_candles=fetch)
    result = _capture_with_retry(feed, wait=delays.append)
    assert result is cap
    assert len(calls) == 3
    assert delays == [1, 2]


def test_initial_public_capture_exhaustion_cannot_create_an_account(tmp_path):
    def unavailable(*args):
        raise URLError(ConnectionResetError(54, "Connection reset by peer"))

    feed = SimpleNamespace(capture_candles=unavailable)
    delays = []
    with pytest.raises(ValueError, match="PUBLIC_SOURCE_CONNECTION_FAILED"):
        _capture_with_retry(feed, wait=delays.append)
    assert delays == [1, 2]
    target = tmp_path / "unavailable-paper"
    with pytest.raises(ValueError, match="PUBLIC_SOURCE_CONNECTION_FAILED"):
        prepare(target, feed=feed)
    assert not target.exists()


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_initial_public_access_denial_fails_immediately(status):
    calls = []
    delays = []

    def denied(*args):
        calls.append(True)
        raise HTTPError("https://api.coinbase.com/", status, "failure", {}, None)

    with pytest.raises(ValueError, match=f"PUBLIC_HTTP_{status}"):
        _capture_with_retry(SimpleNamespace(capture_candles=denied), wait=delays.append)
    assert len(calls) == 1
    assert delays == []


def test_initial_public_server_5xx_retries_with_a_bound():
    calls = []

    def unavailable(*args):
        calls.append(True)
        raise HTTPError("https://api.coinbase.com/", 503, "service unavailable", {}, None)

    with pytest.raises(ValueError, match="PUBLIC_HTTP_503"):
        _capture_with_retry(
            SimpleNamespace(capture_candles=unavailable), wait=lambda _: None
        )
    assert len(calls) == 3



def renewal_capture(tmp_path, *, session_open="2020-01-01T00:15:00Z",
                    session_close="2020-01-04T00:00:00Z"):
    cap, _ = capture(tmp_path)
    cap.product["fcm_trading_session_details"].update(
        open_time=session_open, close_time=session_close
    )
    return cap


def setup_for_renewal(tmp_path):
    initial, _ = capture(tmp_path)
    root = tmp_path / "renewable"
    prepare(root, feed=SimpleNamespace(capture_candles=lambda *a, **k: initial))
    return root, initial


def renewal_feed(cap):
    return SimpleNamespace(capture_candles=lambda *a, **k: cap)


def test_renew_session_publishes_verified_generation_without_mutating_paper(tmp_path):
    root, initial = setup_for_renewal(tmp_path)
    renewed = copy.deepcopy(initial)
    renewed.product["fcm_trading_session_details"].update(
        open_time="2020-01-01T00:15:00Z", close_time="2020-01-04T00:00:00Z"
    )
    setup_before = (root / "setup.json").read_bytes()
    before = PaperStore(root / "paper.sqlite").inspect("bip-paper")
    result = renew_session(root, feed=renewal_feed(renewed), now=renewed.captured_at)
    profile = json.loads((root / "setup.json").read_text())
    generation = root / profile["active_session_generation"]
    event = json.loads((generation / "renewal.json").read_text())
    assert result["status"] == "SESSION_RENEWED_PAPER_ONLY"
    assert profile["active_session_generation"].startswith("session-generations/")
    assert profile["contract_sha256"] == event["contract_fingerprint"]
    assert profile["session_coverage_end_utc"] == "2020-01-04T00:00:00+00:00"
    assert event["paper_account_mutated"] is False
    assert event["halt_reset"] is False
    assert event["observation_evidence"]["run_id"]
    assert set(event["before_files"]) == {"setup", "contract", "calendar"}
    assert all((generation / item["path"]).is_file()
               for item in event["before_files"].values())
    assert (generation / "calendar.json").is_file()
    assert (generation / "contract.json").is_file()
    assert (generation / "public-metadata.json").is_file()
    assert PaperStore(root / "paper.sqlite").inspect("bip-paper") == before
    args = runner_args(root, "run")
    assert str(generation / "contract.json") == args[args.index("--contract-spec") + 1]
    assert str(generation / "calendar.json") == args[args.index("--calendar") + 1]
    assert (root / "setup.json").read_bytes() != setup_before


def test_renew_session_refuses_unchanged_session_without_repointing_setup(tmp_path):
    root, initial = setup_for_renewal(tmp_path)
    before = (root / "setup.json").read_bytes()
    with pytest.raises(ValueError, match="PUBLIC_SESSION_UNCHANGED"):
        renew_session(root, feed=renewal_feed(initial), now=initial.captured_at)
    assert (root / "setup.json").read_bytes() == before
    assert not (root / "session-generations").exists()


@pytest.mark.parametrize("change,match", [
    ("closed", "PUBLIC_SESSION_CLOSED"),
    ("maintenance", "PUBLIC_SESSION_MAINTENANCE"),
    ("contract", "contract specification"),
    ("coverage", "PUBLIC_CAPTURE_STALE_OR_INCOMPLETE"),
])
def test_renew_session_fails_closed_on_invalid_source_without_setup_change(
    tmp_path, change, match
):
    root, initial = setup_for_renewal(tmp_path)
    changed = copy.deepcopy(initial)
    changed.product["fcm_trading_session_details"].update(
        open_time="2020-01-01T00:15:00Z", close_time="2020-01-04T00:00:00Z"
    )
    if change == "closed":
        changed.product["fcm_trading_session_details"]["is_session_open"] = False
    elif change == "maintenance":
        changed.product["fcm_trading_session_details"]["maintenance"] = {
            "start_time": "2020-01-02T00:00:00Z",
            "end_time": "2020-01-02T00:15:00Z",
        }
    elif change == "contract":
        changed.product["price_increment"] = "10"
    elif change == "coverage":
        changed.bars = changed.bars.iloc[1:]
    before = (root / "setup.json").read_bytes()
    with pytest.raises(ValueError, match=match):
        renew_session(root, feed=renewal_feed(changed), now=changed.captured_at)
    assert (root / "setup.json").read_bytes() == before


def test_renewal_crash_before_atomic_setup_pointer_keeps_old_generation_active(tmp_path):
    root, initial = setup_for_renewal(tmp_path)
    changed = copy.deepcopy(initial)
    changed.product["fcm_trading_session_details"].update(
        open_time="2020-01-01T00:15:00Z", close_time="2020-01-04T00:00:00Z"
    )
    before = (root / "setup.json").read_bytes()

    def crash(_generation):
        raise RuntimeError("simulated crash before pointer commit")

    with pytest.raises(RuntimeError, match="simulated crash"):
        renew_session(root, feed=renewal_feed(changed), now=changed.captured_at,
                      before_commit_hook=crash)
    assert (root / "setup.json").read_bytes() == before
    assert not json.loads((root / "setup.json").read_text()).get("active_session_generation")
    # Orphaned generation is immutable evidence, not an active configuration.
    assert list((root / "session-generations").iterdir())


def test_renewal_never_clears_a_persisted_halt(tmp_path):
    root, initial = setup_for_renewal(tmp_path)
    paper = PaperStore(root / "paper.sqlite")
    paper.halt("bip-paper", "operator review required", at_utc="2020-01-02T00:00:00Z")
    changed = copy.deepcopy(initial)
    changed.product["fcm_trading_session_details"].update(
        open_time="2020-01-01T00:15:00Z", close_time="2020-01-04T00:00:00Z"
    )
    result = renew_session(root, feed=renewal_feed(changed), now=changed.captured_at)
    assert result["paper_halted"] is True
    assert result["halt_reset"] is False
    assert bool(PaperStore(root / "paper.sqlite").inspect("bip-paper")["halted"])


def test_renewal_rejects_concurrent_worker_lock_without_setup_mutation(tmp_path):
    root, initial = setup_for_renewal(tmp_path)
    changed = copy.deepcopy(initial)
    changed.product["fcm_trading_session_details"].update(
        open_time="2020-01-01T00:15:00Z", close_time="2020-01-04T00:00:00Z"
    )
    before = (root / "setup.json").read_bytes()
    with account_lock(root / "paper.sqlite", "bip-paper"):
        with pytest.raises(WorkerAlreadyRunning):
            renew_session(root, feed=renewal_feed(changed), now=changed.captured_at)
    assert (root / "setup.json").read_bytes() == before


def test_checkpoint_continuity_refuses_unobserved_gap():
    bars = pd.DataFrame(
        {"close": [10, 11]},
        index=pd.to_datetime(["2026-10-17T19:45:00Z", "2026-10-17T20:00:00Z"]),
    )
    dataset = SimpleNamespace(
        start_utc="2026-10-17T19:45:00Z",
        end_utc="2026-10-17T20:15:00Z",
        bars=bars,
    )
    state = {"risk_events": [{"data": {
        "type": "CHECKPOINT", "closed_bar_utc": "2026-10-17T19:45:00Z"
    }}], "fills": []}
    assert _checkpoint_continuity(state, dataset) == pd.Timestamp("2026-10-17T19:45:00Z")
    dataset.start_utc = "2026-10-17T20:00:00Z"
    with pytest.raises(ValueError, match="UNOBSERVED_CHECKPOINT_GAP"):
        _checkpoint_continuity(state, dataset)



def test_explicit_reset_halt_requires_reason_and_fresh_same_session_health(tmp_path):
    root, initial = setup_for_renewal(tmp_path)
    paper = PaperStore(root / "paper.sqlite")
    paper.halt("bip-paper", "operator review required", at_utc="2020-01-02T00:00:00Z")
    changed = copy.deepcopy(initial)
    changed.product["fcm_trading_session_details"].update(
        open_time="2020-01-01T00:15:00Z", close_time="2020-01-04T00:00:00Z"
    )
    renew_session(root, feed=renewal_feed(changed), now=changed.captured_at)
    with pytest.raises(ValueError, match="at least 8 characters"):
        reset_halt(root, "review", feed=renewal_feed(changed), now=changed.captured_at)
    result = reset_halt(
        root, "Reviewed outage and verified fresh session health",
        feed=renewal_feed(changed), now=changed.captured_at,
    )
    assert result["status"] == "PAPER_HALT_RESET_AFTER_FRESH_HEALTH_GATE"
    assert result["paper_halted"] is False
    assert bool(PaperStore(root / "paper.sqlite").inspect("bip-paper")["halted"]) is False
