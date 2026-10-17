# SQLite futures provenance store (V2 foundation)

This is a **local, research-only** SQLite storage layer, independent of
MetaTrader, exchange order APIs, and any web server. It adds no dependencies
beyond the current Python/Pandas installation.

## Modules and data contract

- `src/metatr/futures_store.py`: `FuturesSQLiteStore`, schema version `1`.
- `raw_snapshots`: immutable source response JSON, metadata JSON, market key,
  source, UTC capture time and SHA-256 checksum. Repeated identical captures are
  idempotent; a prior copy is never silently rewritten.
- `verified_bars`: `market_key` + UTC candle-open primary key, numeric OHLCV,
  reference to first raw snapshot. Existing rows cannot be overwritten by a
  different price or volume: contradictory re-ingestion raises an exception and
  rolls back atomically.
- The same `Market` identity already used in `futures_core` is the partition
  key: exchange + asset + instrument + native product ID + timeframe.
- The SQLite `PRAGMA user_version` is checked at open. Newer or unexpected
  schemas are refused instead of silently interpreted as version 1.

## Example from a validated feed capture

```python
from datetime import datetime, timezone

from metatr.futures_core import Market
from metatr.futures_store import FuturesSQLiteStore

market = Market("coinbase", "BTC", "FUTURE", "BIP-20DEC30-CDE", "M15")
store = FuturesSQLiteStore("data/raw/futures.sqlite")

# Supply each value from the SAME Coinbase public request/capture:
# raw_response: dict of the original public candle JSON (not fabricated values)
# verified_product: dict returned by require_bitcoin_future(...)
# closed_bars: dataframe that passed closed_candles(...) validation
# captured_at: timezone-aware UTC timestamp of the observed window
snapshot_id = store.save_snapshot(
    market,
    source="coinbase_public_futures",
    raw_payload=raw_response,
    contract_metadata=verified_product,
    bars=closed_bars,
    asof=captured_at,
)
restored = store.read_bars(market, limit=300)
source_record = store.read_snapshot(snapshot_id)
```

The above is a **composition example**, not an executable script until the
capture variables are supplied. The current Coinbase CLI does **not** write to
SQLite automatically. A follow-up change will pass an atomic/raw capture
through the feed and let the CLI ingest it with provenance. Do not fabricate
raw payloads to satisfy storage APIs.

## Data protection and operational notes

- Store **only public market API responses**. Never save private-account
  responses, access tokens, or API keys in raw snapshots.
- The `data/raw/` directory is already Git-ignored. Never commit live data.
- Each snapshot must include metadata whose `product_id` matches `Market`.
- All bars must have UTC-aware timestamps, closed before `asof`, correctly
  aligned to the market timeframe, contiguous within the incoming batch and
  valid OHLCV. No gap interpolation or spot substitution is performed.
- `save_snapshot` commits bars + raw JSON in one transaction, or nothing.
- This first version uses SQLite `REAL` to hold research bars. Decimal/tick
  precision for execution and venue-specific correction handling must be
  designed before paper/live execution. Store raw JSON for exact recovery.
- This is not yet a streaming collector, a session-calendar backfill job, an
  experiment registry, or a portfolio/execution database.

## Verification

```bash
python -m pytest -q tests/test_futures_store.py
python -m pytest -q
ruff check .
```

Unit tests cover persistence across process reopen, raw record recovery,
idempotent inserts, per-market isolation, contradictory candle rollback,
still-forming bars, time alignment, contract mismatch and schema refusal.
