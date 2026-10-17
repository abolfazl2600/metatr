import copy
from dataclasses import replace
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pandas as pd
import pytest

from metatr.contract_economics import native_contract_fingerprint
from metatr.display_fixture import fixture_dataset
from metatr.paper_setup import MARKET, _capture_with_retry, main, prepare, runner_args


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
