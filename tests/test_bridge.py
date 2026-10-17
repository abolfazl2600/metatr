import json

import pytest

from metatr.bridge import analyze_bridge_snapshot, main
from metatr.data import sample_bars


def _snapshot(path, symbol="EURUSD", timeframe="M15", schema=1, count=90):
    bars = sample_bars(count)
    frame = bars.reset_index().rename(columns={"index": "timestamp"})
    frame["timestamp"] = (bars.index.asi8 // 1_000_000_000).astype("int64")
    frame["symbol"] = symbol
    frame["timeframe"] = timeframe
    frame["schema_version"] = schema
    frame.to_csv(path, index=False)


def test_bridge_reads_closed_candles(tmp_path):
    path = tmp_path / "bridge.csv"
    _snapshot(path)
    signal = analyze_bridge_snapshot(path, symbol="EURUSD", timeframe="M15")
    assert signal.source == "bridge"
    assert signal.synthetic is False
    assert signal.action in {"BUY", "SELL", "NO_TRADE"}


def test_bridge_rejects_wrong_symbol(tmp_path):
    path = tmp_path / "bridge.csv"
    _snapshot(path, symbol="GBPUSD")
    with pytest.raises(ValueError, match="symbol"):
        analyze_bridge_snapshot(path, symbol="EURUSD", timeframe="M15")


def test_bridge_rejects_wrong_timeframe(tmp_path):
    path = tmp_path / "bridge.csv"
    _snapshot(path, timeframe="H1")
    with pytest.raises(ValueError, match="timeframe"):
        analyze_bridge_snapshot(path, symbol="EURUSD", timeframe="M15")


def test_bridge_rejects_wrong_schema(tmp_path):
    path = tmp_path / "bridge.csv"
    _snapshot(path, schema=9)
    with pytest.raises(ValueError, match="schema"):
        analyze_bridge_snapshot(path, symbol="EURUSD", timeframe="M15")


def test_bridge_insufficient_history(tmp_path):
    path = tmp_path / "bridge.csv"
    _snapshot(path, count=51)
    with pytest.raises(ValueError, match="At least"):
        analyze_bridge_snapshot(path, symbol="EURUSD", timeframe="M15")


def test_bridge_cli_one_shot(tmp_path, capsys):
    path = tmp_path / "bridge.csv"
    _snapshot(path)
    assert main(["--file", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["symbol"] == "EURUSD"
    assert payload["source"] == "bridge"


def test_bridge_cli_missing_file(tmp_path, capsys):
    assert main(["--file", str(tmp_path / "missing.csv")]) == 1
    assert "Bridge error" in capsys.readouterr().err
