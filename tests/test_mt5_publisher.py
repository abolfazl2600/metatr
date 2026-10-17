from dataclasses import replace

import pytest
from test_contract_economics import MARKET

from metatr.display_fixture import fixture_dataset
from metatr.mt5_publish_cli import main
from metatr.mt5_publisher import market_directory, publish, read_publication
from metatr.research import replay
from metatr.strategies import StrategyDecision

NOW = "2020-01-02T00:00:00Z"


def inputs(tmp_path):
    dataset, spec, contract = fixture_dataset(MARKET, NOW, tmp_path)
    decisions = [StrategyDecision(**d) for d in replay(dataset, spec)["decisions"]]
    return dataset, decisions, spec, contract


def test_protocol_roundtrip_idempotence_and_fixture_cli(tmp_path):
    args = inputs(tmp_path)
    target = publish(*args, tmp_path / "common", now_utc=NOW)
    before = target.read_bytes()
    assert publish(*args, tmp_path / "common", now_utc=NOW).read_bytes() == before
    result = read_publication(target.parent, MARKET, now_utc=NOW)
    assert len(result["bars"]) == 80 and len(result["signals"]) == 80
    assert result["manifest"]["label"] == "EXTERNAL_SIMULATED_DATA"
    assert (
        main(
            [
                "--fixture",
                "--market",
                MARKET.key,
                "--output-dir",
                str(tmp_path / "cli"),
                "--now-utc",
                NOW,
            ]
        )
        == 0
    )


@pytest.mark.parametrize("kind", ["stale", "market", "decision", "corrupt", "missing", "schema"])
def test_corrupt_mixed_stale_inputs_fail_closed(tmp_path, kind):
    args = list(inputs(tmp_path))
    if kind == "stale":
        with pytest.raises(ValueError):
            publish(*args, tmp_path / "common", now_utc="2020-01-03T00:00:00Z")
        return
    if kind == "decision":
        args[1][0] = replace(args[1][0], data_sha256="a" * 64)
        with pytest.raises(ValueError):
            publish(*args, tmp_path / "common", now_utc=NOW)
        return
    target = publish(*args, tmp_path / "common", now_utc=NOW)
    result = read_publication(target.parent, MARKET, now_utc=NOW)
    if kind == "market":
        with pytest.raises(ValueError):
            read_publication(
                target.parent, replace(MARKET, product_id="BIT-20DEC30-CDE"), now_utc=NOW
            )
        return
    if kind == "corrupt":
        (target.parent / result["manifest"]["bars_file"]).write_text("corrupt")
    elif kind == "missing":
        (target.parent / result["manifest"]["signals_file"]).unlink()
    else:
        target.write_bytes(target.read_bytes().replace(b"\n1,", b"\n99,"))
    with pytest.raises((ValueError, OSError)):
        read_publication(target.parent, MARKET, now_utc=NOW)


def test_interrupted_manifest_preserves_old_and_invalid_signal_is_no_trade(tmp_path, monkeypatch):
    import metatr.mt5_publisher as module

    args = list(inputs(tmp_path))
    target = publish(*args, tmp_path / "common", now_utc=NOW)
    before = target.read_bytes()
    real = module._atomic

    def interrupted(path, data, **kwargs):
        if path.name == "manifest.csv":
            raise OSError("interrupted before commit")
        real(path, data, **kwargs)

    monkeypatch.setattr(module, "_atomic", interrupted)
    args[1][-1] = replace(args[1][-1], action="bad action")
    with pytest.raises(OSError):
        publish(*args, tmp_path / "common", now_utc=NOW)
    assert target.read_bytes() == before
    assert read_publication(target.parent, MARKET, now_utc=NOW)["manifest"]["status"] == "VALID"
    monkeypatch.setattr(module, "_atomic", real)
    publish(*args, tmp_path / "common", now_utc=NOW)
    result = read_publication(target.parent, MARKET, now_utc=NOW)
    assert result["manifest"]["status"] == "INVALID"
    assert result["signals"][-1]["action"] == "NO_TRADE"
    assert market_directory(tmp_path / "common", MARKET) == target.parent


def test_concurrent_reader_and_bounded_generations(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    args = list(inputs(tmp_path))
    target = publish(*args, tmp_path / "common", now_utc=NOW)

    def reading():
        snapshots = []
        for _ in range(15):
            try:
                snapshots.append(read_publication(target.parent, MARKET, now_utc=NOW))
            except FileNotFoundError:
                # A slow reader may race retention; it must retain STALE UI and retry.
                continue
        return snapshots

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reading)
        for i in range(4):
            args[1][-1] = replace(args[1][-1], reason=f"variant {i}")
            publish(*args, tmp_path / "common", now_utc=NOW)
        observed = future.result()
    assert observed
    assert all(len(r["bars"]) == 80 and len(r["signals"]) == 80 for r in observed)
    assert len(list(target.parent.glob("bars_*.csv"))) <= 4
