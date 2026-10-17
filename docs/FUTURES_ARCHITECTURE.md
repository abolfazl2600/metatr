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


## October 2026 data-integrity milestone (read-only)

- **Underlying identity:** `product_type=FUTURE` is necessary but not sufficient. For Coinbase
  Derivatives Exchange (CDE), the currently supported **Bitcoin-only** contract families
  are `BIP-<DDMMMYY>-CDE` (perpetual-style) and `BIT-<DDMMMYY>-CDE` (dated).
  Verification checks the native product ID family and, when supplied, the matching
  `future_product_details.contract_code` plus non-contradictory base/quote identifiers.
  Bitcoin Cash families `BCP`/`BCH` are rejected regardless of the word "Bitcoin"
  in their display names. Unknown contract families fail closed until independently verified.
- **Perpetual ≠ dated:** `BIP`'s far-future `contract_expiry` is treated as opaque metadata;
  it is not a liquidation deadline. `BIT` requires a timezone-aware expiry timestamp.
  The public exchange metadata is validated conservatively. Contract eligibility for
  real-money trading, margin and legal access is **not** established by this check.
- **Historical completeness:** Candles are sorted into ascending UTC time, limited to
  the requested window, and validated for OHLCV, timeframe alignment and continuity.
  Duplicate bars, missing bars or gaps produce an error, never a silent spot fallback.
  Known exchange maintenance periods may cause genuine gaps; calendar-aware backfill
  is a future requirement, not a reason to synthesize prices.
- **Live connectivity:** The check below is a network test, not a mock. It may fail with
  403, 429, empty candles or incomplete bars according to access rules and liquidity.
  A successful offline pytest run is not evidence that the live endpoint worked.

### Probe live futures candles without model selection

```bash
python -m metatr.futures_cli --product-id BIP-20DEC30-CDE \
  --timeframe M15 --count 64 --probe-candles
```

This produces `mode=PUBLIC_DATA_PROBE`, `validated_contiguous=true`, UTC range,
contract kind and `execution=DISABLED` **only if** the public Coinbase endpoint
returns the requested valid, complete candle window. It never submits orders or
reads API keys. Use a sufficiently recent short window to avoid known Friday
maintenance gaps. A dedicated GitHub Actions smoke workflow runs the same check
on the development branch and is also manually dispatchable.

### Validation and remaining gates

1. Offline unit/regression: BTC/BCH contract family, contradictory metadata, dated
   expiry, BIP pseudo-expiry, closed candles, sorting, and missing-bar rejection.
2. Live API: independently confirm metadata returned by the actual venue,
   response HTTP status, real M15 candle count, gaps, and geographic access.
3. Before model comparisons: design session-calendar-aware backfill, immutable
   raw-response provenance, recovery/retry policy and a robust out-of-sample test.
4. Before paper trading: exchange-native contract specifications, position risk,
   slippage, taker/maker fees, funding and an auditable order state machine.
5. Before live trading: explicit opt-in, account/legal permission, reconciler and
   risk veto. Current trading execution remains disabled.

Official perp specification (no expiration or final settlement):
https://help.coinbase.com/en/derivatives/perpetual-style-futures/contract-specifications
