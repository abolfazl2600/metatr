"""Explicit synthetic offline fixture for the outbound display integration path."""

import pandas as pd

from metatr.calendars import SessionCalendar
from metatr.contract_economics import ContractSpec
from metatr.data import sample_bars
from metatr.dataset_quality import validate_dataset
from metatr.futures_store import TIMEFRAME_SECONDS, FuturesSQLiteStore
from metatr.model_registry import StrategyRegistry, StrategySpec
from metatr.research import digest
from metatr.strategies import utc


def fixture_dataset(market, asof_utc, workspace):
    if market.product_id != "BIP-20DEC30-CDE":
        raise ValueError("Synthetic display fixture uses explicitly labeled BIP-20DEC30-CDE only")
    seconds = TIMEFRAME_SECONDS[market.timeframe]
    end = utc(asof_utc).floor(pd.Timedelta(seconds=seconds))
    bars = sample_bars(80)
    bars.index = pd.date_range(
        end=end - pd.Timedelta(seconds=seconds),
        periods=80,
        freq=pd.Timedelta(seconds=seconds),
        tz="UTC",
    )
    meta = {
        "product_id": market.product_id,
        "product_type": "FUTURE",
        "future_product_details": {"contract_code": "BIP", "contract_size": "0.01"},
    }
    candles = [
        {"start": str(int(t.timestamp())), **{c: str(row[c]) for c in row.index}}
        for t, row in bars.iterrows()
    ]
    store = FuturesSQLiteStore(workspace / "fixture.sqlite")
    store.save_snapshot(
        market,
        source="synthetic_fixture",
        raw_payload={"candles": candles},
        contract_metadata=meta,
        bars=bars,
        asof=end,
    )
    start = bars.index[0].isoformat()
    calendar = SessionCalendar(
        market, "synthetic_fixture", "v1", "synthetic-test-only", "UTC", start, end.isoformat()
    )
    _, dataset = validate_dataset(store, market, start, end, end, calendar=calendar)
    registry = StrategyRegistry(workspace / "registry.sqlite")
    spec = registry.register(
        StrategySpec.create(
            market,
            "momentum",
            "1.0.0",
            "momentum",
            {"lookback": 8, "threshold": 0.001},
            created_at=start,
            status="research",
        )
    )
    contract = ContractSpec(
        market,
        "fixture-v1",
        "BIP",
        "PERPETUAL_STYLE",
        "BTC",
        "USD",
        "USD",
        "0.01",
        "1",
        "1",
        "1",
        0,
        "synthetic_fixture",
        "v1",
        "USD",
        start,
        "synthetic-test-only",
        digest(meta),
    )
    return dataset, spec, contract
