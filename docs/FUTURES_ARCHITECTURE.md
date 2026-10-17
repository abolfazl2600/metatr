# Multi-exchange / multi-asset / multi-model futures architecture

## Scope and safety (current slice)
This slice adds a **read-only** Coinbase Advanced Trade public market-data adapter, strict BTC futures product discovery, and per-market strategy-candidate selection. It never sends orders. The existing EURUSD MT5 bridge is unchanged. Market access (and any eventual live trading) is subject to the user's location, verified account permissions, and local laws. No credentials are needed for public endpoints.

## Data flow

Coinbase Public Products → verify product_type=FUTURE + BTC → public candles → UTC closed-bar validation → market-specific profile → causal historical research score → LONG/SHORT/NO_TRADE JSON → [future MT5 dashboard bridge]

- Source identity is (exchange, asset, instrument, product_id, timeframe). A future contract expiry **must not** be hidden behind a generic BTC-USD spot label.
- Profiles are defined in config/markets.json; exact contracts override wildcard-per-product profiles. Missing profiles **fail closed**.
- Future integrations implement independent feeds per exchange and derivative type (Coinbase global derivatives is a **separate API**, and cannot reuse this US futures connector). Every adapter must verify actual instrument identity and contract spec.
- Each market may have different models, parameters, fee assumptions and validation rules. Candidates currently are **research baselines** (ema_cross and momentum), not trained AI models. New models can later be registered behind the same predict() interface, with versioned artifacts and walk-forward retraining.
- Candidate scoring uses only earlier closed candles. It includes an assumed per-trade round-trip cost; it is **not** a full futures backtest, does not model liquidation, funding, spread or contract rollover, and does not prove profitability.
- If no candidate passes minimum evidence thresholds, output is NO_TRADE and selected_model is null.
- Actual execution is disabled (execution=DISABLED). Future authenticated exchange-specific execution adapters will need separate credentials, account permissions, idempotent order IDs, venue confirmations, risk controls, trade limits, emergency stop, reconciliation, journaling and explicit live enablement; no orders via MT5.

## Commands on macOS (from repository root, Python 3.11+)

First pull, install and test:

    git pull origin main
    source .venv/bin/activate
    python -m pip install -e '.[dev]'
    python -m pytest -q
    python -m metatr.futures_cli --list

Read the actual returned futures product_id. **Never substitute BTC-USD**: it is commonly a SPOT product. Choose a valid BTC future from the list, then:

    python -m metatr.futures_cli --product-id 'REAL_BTC_FUTURES_PRODUCT_ID' --timeframe M15 --count 300 --output runtime/coinbase_btc_futures_signal.json

Replace the placeholder with an actual ID. If no future is listed or the API refuses access, STOP; do not silently switch to spot. The --output file is an atomic snapshot for a *future* MT5 display adapter: MT5 import/display has not been implemented in this slice.

## Planned layers
1. Market metadata and provenance: exchange IDs, venue, contract specification, currency, tick/lot/contract value, expiry, funding, session, snapshot timestamp and gaps.
2. Durable store: PostgreSQL/Timescale or Parquet with immutable raw payloads, schema versions, UTC, exchange-native IDs, backfill and resumable WebSocket.
3. Multi-model lab: per market/timeframe strategy/ML registry, reproducible features, purged walk-forward validation, realistic slippage/fees, portfolio simulation and model retirement.
4. Selection/risk engine: calibrated scores, regime-aware gating, drift monitors, no-lookahead, correlated-market exposure and veto/NO_TRADE by default.
5. Execution: verified exchange adapters, sandbox/paper first, positions/fills reconciliation, rate limits, circuit breakers and explicit live opt-in.
6. MT5 display: Python publishes bars, selections, decisions, fills and PnL; MQL5 custom symbol/indicators display **external** trades without placing MT5 orders.

## Official references
- https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/list-public-products
- https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product
- https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-candles
