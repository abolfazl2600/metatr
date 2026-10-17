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
