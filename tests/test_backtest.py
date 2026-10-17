from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest
from test_contract_economics import MARKET, economics
from test_dataset_quality import save

from metatr.backtest import BacktestEngine, FuturesLedger, SimulationRisk, apply_fill
from metatr.calendars import SessionCalendar
from metatr.contract_economics import SimulatedFill
from metatr.dataset_quality import validate_dataset
from metatr.futures_store import FuturesSQLiteStore
from metatr.model_registry import StrategySpec
from metatr.research import digest


def fixture(tmp_path, values=(10000, 10010, 10020, 10000, 9990, 9980)):
    meta, spec, costs, _, _ = economics()
    index = pd.date_range("2020-01-01", periods=len(values), freq="15min", tz="UTC")
    bars = pd.DataFrame({"open": values, "close": values,
                         "high": [v + 100 for v in values], "low": [v - 100 for v in values],
                         "volume": [100] * len(values)}, index=index)
    store = FuturesSQLiteStore(tmp_path / "backtest.sqlite")
    save(store, bars)
    end = index[-1] + pd.Timedelta(minutes=15)
    calendar = SessionCalendar(MARKET, "fixture", "v1", "synthetic-test-only", "UTC",
                               index[0].isoformat(), end.isoformat())
    _, dataset = validate_dataset(store, MARKET, index[0], end, end, calendar=calendar)
    strategy = StrategySpec.create(MARKET, "momentum", "1.0.0", "momentum",
                                   {"lookback": 1, "threshold": 0},
                                   created_at="2020-01-01T00:00:00Z")
    # Match the actual fixture metadata digest; all values remain explicitly synthetic.
    spec = replace(spec, metadata_sha256=digest(dataset.provenance[0]["contract_metadata"]))
    risk = SimulationRisk("1000", "2", "10000", "0.5")
    return dataset, strategy, spec, costs, risk


def test_next_bar_reversal_repeat_and_no_lookahead(tmp_path):
    args = fixture(tmp_path)
    result = BacktestEngine().run(*args).to_dict()
    assert result == BacktestEngine().run(*args).to_dict()
    assert result["fills"][0]["timestamp"] == "2020-01-01T00:30:00+00:00"
    assert result["fills"][0]["price"] == "10021"
    assert result["fills"][1]["quantity"] == "4"
    assert result["fills"][1]["side"] == "SELL"
    assert len(result["trades"]) == 1
    assert result["ledger"]["contracts"] == "-2"
    other_path = tmp_path / "other"
    other_path.mkdir()
    modified = fixture(other_path, (10000, 10010, 10020, 10500, 10600, 10700))
    other = BacktestEngine().run(*modified).to_dict()
    assert result["fills"][0] == other["fills"][0]
    assert [d["action"] for d in result["decisions"][:3]] == [
        d["action"] for d in other["decisions"][:3]]


def test_hand_checked_long_short_reverse_fee_accounting():
    _, spec, _, _, _ = economics()
    def fill(side, quantity, price, fee):
        return SimulatedFill(MARKET.key, "2020-01-01T00:15:00Z", side, quantity, price,
                             str(Decimal(quantity) * Decimal(price) * Decimal("0.01")),
                             fee, "USD", spec.fingerprint, "fixture")
    ledger = FuturesLedger("1000")
    ledger, trade = apply_fill(ledger, fill("BUY", "2", "10000", "1"), spec)
    assert Decimal(ledger.cash) == 999 and trade is None
    ledger, trade = apply_fill(ledger, fill("SELL", "4", "10100", "2"), spec)
    assert Decimal(ledger.cash) == Decimal("999")  # +2 realized, -2 fee
    assert Decimal(trade["net_pnl"]) == 0  # +2 gross, -1 entry, -1 exit
    assert ledger.contracts == "-2" and ledger.open_costs == "1"
    ledger, trade = apply_fill(ledger, fill("BUY", "2", "10000", "1"), spec)
    assert Decimal(ledger.cash) == 1000
    assert Decimal(trade["net_pnl"]) == 0
    assert Decimal(ledger.fees) == 4 and Decimal(ledger.realized_pnl) == 4
    assert ledger.contracts == "0" and Decimal(ledger.open_costs) == 0


def test_flat_expiry_invalid_quality_and_risk(tmp_path):
    args = fixture(tmp_path, (10000,) * 4)
    assert not BacktestEngine().run(*args).to_dict()["fills"]
    with pytest.raises(ValueError):
        BacktestEngine().run(replace(args[0], validation_json="null"), *args[1:])
    with pytest.raises(ValueError):
        BacktestEngine().run(*args[:4], replace(args[4], initial_equity="-1"))
    args2 = args
    # A final event has no invented next-bar fill; use the benchmark for a final intent.
    result = BacktestEngine().run(*args2, decision_start=3, baseline="buy_hold").to_dict()
    assert not result["fills"]
    assert result["events"][-1]["kind"] == "INTENT_EXPIRED"


def test_fee_buffer_veto_and_latched_simulation_halt(tmp_path):
    args = fixture(tmp_path)
    result = BacktestEngine().run(*args[:4], replace(args[4], max_notional="1")).to_dict()
    assert not result["fills"]
    assert any(e["kind"] == "FILL_REJECTED" for e in result["events"])
    result = BacktestEngine().run(*args[:4], replace(
        args[4], max_drawdown_fraction="0.00000001")).to_dict()
    assert result["status"] == "HALTED"
    assert result["events"][-1]["kind"] == "RISK_HALT"
