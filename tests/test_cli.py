import json

from metatr.cli import main


def test_cli_sample_output(capsys):
    assert main(["--source", "sample", "--count", "100"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "sample"
    assert payload["synthetic"] is True
    assert payload["action"] in {"BUY", "SELL", "NO_TRADE"}


def test_cli_writes_file(tmp_path):
    path = tmp_path / "signal.json"
    main(["--count", "100", "--output", str(path)])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbol"] == "EURUSD"
