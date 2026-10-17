from dataclasses import replace
from decimal import Decimal

import pytest

from metatr.contract_economics import (
    ContractSpec,
    CostScenario,
    SimulatedOrder,
    estimate_fill,
    value_position,
)
from metatr.futures_core import Market
from metatr.research import digest

MARKET = Market("coinbase", "BTC", "FUTURE", "BIP-20DEC30-CDE", "M15")


def economics():
    metadata = {"product_id": MARKET.product_id, "product_type": "FUTURE",
                "future_product_details": {"contract_code": "BIP", "contract_size": "0.01"}}
    spec = ContractSpec(MARKET, "fixture-v1", "BIP", "PERPETUAL_STYLE", "BTC", "USD", "USD",
                        "0.01", "1", "1", "1", 0, "fixture", "v1", "USD",
                        "2020-01-01T00:00:00Z", "synthetic fixture only", digest(metadata))
    costs = CostScenario("fixture", "v1", "2020-01-01T00:00:00Z", "synthetic fixture",
                         "Explicit hypothetical costs, no verified margin/liquidation",
                         "USD", "0", "10", "0", "1", "0.1", "base",
                         "ASSUMED_ABSOLUTE_NOTIONAL_PER_BAR", "0")
    order = SimulatedOrder(MARKET, "BUY", "2", "2020-01-01T00:00:00Z",
                           "2020-01-01T00:15:00Z")
    bar = {"timestamp": "2020-01-01T00:15:00Z", "open": "10000", "high": "10020",
           "low": "9980", "close": "10010", "volume": "100"}
    return metadata, spec, costs, order, bar


def test_exact_multiplier_fee_and_pnl():
    meta, spec, costs, order, bar = economics()
    fill = estimate_fill(order, bar, spec, costs, meta)
    assert fill.price == "10001"
    assert Decimal(fill.notional) == Decimal("200.02")
    assert Decimal(fill.fee) == Decimal("0.20002")
    assert value_position("2", "10001", "10011", spec)["unrealized_pnl"] == "0.20"
    assert value_position("-2", "10001", "9991", spec)["unrealized_pnl"] == "0.20"
    sell = estimate_fill(replace(order, side="SELL"), bar, spec, costs, meta)
    assert sell.price == "9999"
    expensive = replace(costs, taker_fee_bps="20", spread_bps="10")
    other = estimate_fill(order, bar, spec, expensive, meta)
    assert Decimal(other.price) > Decimal(fill.price)
    assert Decimal(other.fee) > Decimal(fill.fee)
    assert costs.fingerprint != expensive.fingerprint


@pytest.mark.parametrize("kind", ["same_bar", "quantity", "float", "liquidity", "spot",
                                  "size", "funding", "fee", "ohlc", "volume", "expiry"])
def test_unsafe_economics_refused(kind):
    meta, spec, costs, order, bar = economics()
    if kind == "same_bar":
        bar["timestamp"] = order.decision_open_utc
    elif kind == "quantity":
        order = replace(order, quantity="1.5")
    elif kind == "float":
        order = replace(order, quantity=2.0)
    elif kind == "liquidity":
        bar["volume"] = "0"
    elif kind == "spot":
        order = replace(order, market=replace(MARKET, product_id="BTC-USD"))
    elif kind == "size":
        spec = replace(spec, multiplier="1")
    elif kind == "funding":
        costs = replace(costs, funding_model="unknown")
    elif kind == "fee":
        costs = replace(costs, taker_fee_bps="-1")
    elif kind == "ohlc":
        bar["high"] = "1"
    elif kind == "volume":
        bar["volume"] = "-1"
    else:
        market = replace(MARKET, product_id="BIT-20DEC30-CDE")
        meta = {"product_id": market.product_id, "product_type": "FUTURE",
                "future_product_details": {"contract_code": "BIT", "contract_size": "0.01",
                                           "contract_expiry": "2020-01-01T00:00:00Z"}}
        spec = replace(spec, market=market, family="BIT", kind="DATED",
                       metadata_sha256=digest(meta), expiry_utc="2020-01-01T00:00:00Z")
        order = replace(order, market=market)
    with pytest.raises(ValueError):
        estimate_fill(order, bar, spec, costs, meta)
