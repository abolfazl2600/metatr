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


def test_custom_chart_auto_fit_uses_verified_ohlc_not_zero_quote():
    source = Path("mql5/Experts/MetatrFuturesViewer.mq5").read_text()
    assert '#property version "1.03"' in source
    assert "input bool AutoFitExternalPrices=true;" in source
    assert "FitRecentBars<16 || FitRecentBars>500" in source
    assert "low=MathMin(low,rates[i].low);" in source
    assert "high=MathMax(high,rates[i].high);" in source
    assert "MathMax((high-low)*0.08,high*0.0005)" in source
    assert "CHART_FIXED_MIN,bottom" in source
    assert "CHART_FIXED_MAX,top" in source
    assert "CHART_SCALEFIX,true" in source
    assert "CHART_CANDLES" in source
    assert "CHART_SHOW_BID_LINE,false" in source
    assert "CHART_SHOW_ASK_LINE,false" in source
    assert "CHART_SHOW_LAST_LINE,false" in source
    assert "if(!ConfigureDisplayChart(rates,count)) return false;" in source
    assert "ChartSetSymbolPeriod(display_chart,custom_symbol,PERIOD_M15)" in source
    # Successful CSV manifest + row checks must happen before any chart mutation.
    assert source.index('if(previous!=latest) return Fail("missing latest signal");') < (
        source.index("ConfigureDisplayChart(rates,count)) return false;")
    )
    assert "CustomRatesReplace" in source
    assert "SYMBOL_TRADE_MODE_DISABLED" in source


def test_auto_fit_can_be_disabled_for_operator_control_without_broker_orders():
    source = Path("mql5/Experts/MetatrFuturesViewer.mq5").read_text()
    assert "if(!AutoFitExternalPrices)" in source
    assert "return true; // Operator may explicitly disable managed scaling." in source
    assert "bottom<=0 || top<=bottom" in source
    assert "ChartRedraw(display_chart);" in source
    assert not re.search(r"\b(?:OrderSend\w*|CTrade|WebRequest)\s*\(", source)
