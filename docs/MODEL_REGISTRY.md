# Strategy & Model Registry (schema v1)

Issue #11 establishes an **offline research registry**. It **does not execute orders**, select strategies for live trading, fetch account data, or change the legacy read-only futures CLI. All output is explicitly `execution=DISABLED`. Registry status (`draft`, `research`, `retired`) is categorization, **never authorization to trade**.

## Exact, pinned market identity

Every registration is keyed by all **seven** fields:

```text
(exchange, asset, instrument, product_id, timeframe, strategy_id, version)
```

For example, `coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15` and `coinbase:BTC:FUTURE:BIT-20DEC30-CDE:M15` are **different markets**. M5 versus M15, BTC versus BCH, spot versus futures, and two different contract expirations are also different. The native product ID is preserved exactly; contract family and expiry are not inferred from the asset alone. Supply only product IDs verified independently with the public futures discovery API. A registry record does not certify exchange product metadata.

Registration, lookup, retirement and experiment binding require exact identities; `*` is not accepted as a stored market field. Missing exact registrations fail closed rather than selecting another market. Pin `strategy_id` **and** semantic `version` for reproducible research.

## Data layout and migrations

Default database: `data/research/registry.sqlite`, intentionally separate from `data/raw/futures.sqlite`. No schema of the existing futures raw-data store is altered.

Registry SQLite schema **v1**:

```sql
PRAGMA application_id = 1297371730; -- MTRR
PRAGMA user_version = 1;
CREATE TABLE schema_version (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  version INTEGER NOT NULL CHECK (version = 1)
);
CREATE TABLE strategy_versions (
  exchange TEXT NOT NULL, asset TEXT NOT NULL,
  instrument TEXT NOT NULL, product_id TEXT NOT NULL,
  timeframe TEXT NOT NULL, strategy_id TEXT NOT NULL,
  version TEXT NOT NULL, implementation_key TEXT NOT NULL,
  parameters_json TEXT NOT NULL, features_json TEXT NOT NULL,
  warmup_bars INTEGER NOT NULL CHECK (warmup_bars >= 1),
  evaluation_json TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'research', 'retired')),
  config_sha256 TEXT NOT NULL,
  PRIMARY KEY (exchange, asset, instrument, product_id,
               timeframe, strategy_id, version)
) WITHOUT ROWID;
```

Creation is an explicit transaction (`BEGIN IMMEDIATE`) that migrates only a genuinely empty database from version 0 to version 1. Subsequent opens verify both SQLite `application_id`/`user_version` and the `schema_version` row. Unsupported or unrecognized database versions (including a raw futures store) are **rejected**. New schema versions require a separately reviewed migration; no destructive or speculative automatic upgrade runs.

SQLite triggers block updates to version identity, implementation, parameters, requirements, assumptions, timestamp and digest, and block deletion. Only a **monotonic lifecycle status transition** is allowed (`draft` → `research` → `retired`, including direct retirement; retired cannot reactivate). The API currently provides `retire`; new registrations may start in `draft`, `research` or `retired`.

`config_sha256` is the SHA-256 digest of canonical UTF-8 JSON with sorted keys for full market identity, strategy ID/version, allowlisted implementation key, validated parameters, features, warmup and evaluation settings. Creation time and lifecycle status are excluded because they are provenance/lifecycle metadata, not strategy configuration. Hashes detect accidental mismatch; they are **not signatures** and do not protect a compromised SQLite database.

`register` is idempotent if the market, strategy ID, version and config hash are identical (returning the previously stored timestamp and status). Different content under the same pinned version raises `VersionConflict` without changing storage. `register_many` performs a whole-batch atomic insert or rollback, used by the legacy importer. `get`, `resolve`, `list` and `retire` are exact-key operations; `resolve` rejects missing pinned versions. JSON fields are canonical strings in the frozen specification; property access returns fresh dictionaries.

## Explicit legacy profile migration

`config/markets.json` contains `coinbase:BTC:FUTURE:*:M15`. That is a **template**, not a market. Import requires a real, explicitly supplied native Coinbase futures `product_id` and explicit `--allow-wildcard`; otherwise it fails. An exact market profile takes precedence if defined. The importer materializes two independent pinned `1.0.0` specifications without rewriting the legacy config or futures CLI:

| Strategy ID / implementation | Parameters | Warmup |
| --- | --- | --- |
| `ema_cross` | `fast=12`, `slow=26` | 28 bars |
| `momentum` | `lookback=8`, `threshold=0.001` | 9 bars |

Both records retain `evaluation_bars=80`, `min_trades=3`, `round_trip_cost_bps=12`. Unknown implementations and invalid parameters fail closed; only `ema_cross` and `momentum` implementation keys are allowed. No arbitrary module/class names, imports or runtime evaluation may be passed via config. If a different version is needed, register a **new SemVer version** (e.g. `1.0.1`) rather than overwriting `1.0.0`.

## CLI examples

Install from project root (`python -m pip install -e '.[dev]'`) and first obtain a real futures product ID using `python -m metatr.futures_cli --list`.

```bash
# Illustrative native ID; confirm with fresh discovery before registration.
metatr-registry --db data/research/registry.sqlite import-legacy \
  --exchange coinbase --asset BTC --instrument FUTURE \
  --product-id BIP-20DEC30-CDE --timeframe M15 \
  --config config/markets.json --allow-wildcard --version 1.0.0

metatr-registry --db data/research/registry.sqlite list

metatr-registry --db data/research/registry.sqlite show \
  --exchange coinbase --asset BTC --instrument FUTURE \
  --product-id BIP-20DEC30-CDE --timeframe M15 \
  --strategy-id ema_cross --version 1.0.0

metatr-registry --db data/research/registry.sqlite retire \
  --exchange coinbase --asset BTC --instrument FUTURE \
  --product-id BIP-20DEC30-CDE --timeframe M15 \
  --strategy-id ema_cross --version 1.0.0
```

Equivalent Python API:

```python
from metatr.futures_core import Market
from metatr.model_registry import StrategyRegistry, import_legacy_config

market = Market("coinbase", "BTC", "FUTURE", "BIP-20DEC30-CDE", "M15")
registry = StrategyRegistry()
import_legacy_config(registry, market, "config/markets.json", allow_wildcard=True)
selected = registry.resolve(market, "ema_cross", "1.0.0")
print(selected.to_dict())  # research metadata only; execution=DISABLED
```

## Verification and limitations

CI runs offline `python -m pytest -q` and `ruff check .` on Python 3.11 and 3.12. Tests cover market/timeframe isolation, version immutability, digest stability, idempotence, conflict rollback, unsupported DB schema refusal, reopen persistence, retirement, invalid/unknown strategies and explicit wildcard gating.

This issue does not wire the registry into `futures_core.select_model`; the existing read-only futures signal CLI keeps its legacy profile behavior. Subsequent work must pin registry model IDs to **reproducible experiments** rather than silently changing existing research selection or enabling order execution. No assertion of strategy profitability is made.
