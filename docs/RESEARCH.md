# Reproducible offline signal research

This stage replays pinned strategy **events**, with no fill, position, fee or PnL
simulation. Outputs state `research_only=true`, `backtest_validated=false`,
`execution=DISABLED` and top-level `action=NO_TRADE`. Individual baseline events
are not evidence that an executable strategy is profitable.

First collect a verified public futures snapshot (the only network step):

```bash
python -m metatr.futures_cli --product-id BIP-20DEC30-CDE --timeframe M15 \
  --count 300 --probe-candles --store data/raw/futures.sqlite
```

Register the exact contract with the registry importer as described in
[MODEL_REGISTRY.md](MODEL_REGISTRY.md). Choose actual stored UTC candle-open
boundaries; the end is exclusive. The following dates illustrate the syntax,
not a claim that this dataset exists locally:

```bash
python -m metatr.research_cli --offline-only \
  --store data/raw/futures.sqlite --registry data/research/registry.sqlite \
  --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --strategy-id ema_cross --strategy-version 1.0.0 \
  --start-utc 2026-10-16T00:00:00Z --end-utc 2026-10-17T00:00:00Z \
  --seed 0 --output data/research/runs
```

Repeat with `--strategy-id momentum` to inspect baseline event differences.
All inputs are local; the flag documents intent and network access is never
implemented in this entrypoint. Both DB files must already exist. Empty/gapped,
unaligned, open or mixed-market intervals, broken provenance, unknown versions
and retired models exit nonzero without publishing a success result.

The reader takes a consistent SQLite snapshot, checks the known v1 schema,
checks each raw payload digest and content-addressed snapshot ID, verifies BTC
futures metadata, and returns a frozen, hash-checked dataset with copy-on-read
bars. Range convention is **[start,end)** by candle open, aligned to the timeframe,
maximum 100,000 bars. No silent limit truncation or interpolation is permitted.
Every provenance receipt must be available by `--asof-utc` when supplied. Otherwise
as-of is the latest pinned receipt time, independent of the current system clock.
A later quality gate adds verified calendar/session discontinuities.

The versioned manifest records exact Market, pinned strategy/config digest,
raw snapshot IDs, contract metadata, source/data digest, first/last bar/count,
UTC as-of, seed, Python/package versions and available git revision. Run ID is
SHA-256 over canonical manifest/results JSON (excluding its own ID). The manifest
is published to `<output>/<run_id>.json` from a flushed/fsynced temporary file
using an atomic no-overwrite link. Identical publication is idempotent; changed
content at an existing path is refused. No wall-clock generation timestamp enters
the reproducible identity. Compare bytes under the same pinned code/runtime.

Offline tests seed explicitly synthetic fixtures in temporary stores, verify
contract isolation, corrupt snapshots, missing bars, as-of boundaries, repeat
hashes, retired models and CLI behavior. Synthetic fixtures never claim live data.
