"""Static safety guard only; this is deliberately NOT a native compilation test."""

import re
from pathlib import Path


def test_native_viewer_has_no_trading_or_private_network_surface():
    source = Path("mql5/Experts/MetatrFuturesViewer.mq5").read_text()
    assert not re.search(r"\b(?:OrderSend\w*|CTrade|WebRequest|AccountInfo\w*)\s*\(", source)
    assert "void OnTimer()" in source and "EventKillTimer()" in source
    assert "CRYPT_HASH_SHA256" in source and "CustomRatesReplace" in source
    assert "SYMBOL_TRADE_MODE_DISABLED" in source
    assert "STALE/INVALID" in source and "EXTERNAL / SIMULATED DATA" in source


def test_paper_overlay_is_explicitly_nonexecuting_and_keeps_validation():
    source = Path("mql5/Experts/MetatrFuturesViewer.mq5").read_text()
    assert "PAPER SIMULATED SIGNAL / NOT ORDER" in source
    assert "PAPER simulated research signal, NOT an order" in source
    assert "PAPER NO_TRADE" in source
    assert 'm[20]!="DISABLED"' in source
    assert "signal timestamp/model/provenance mismatch" in source
