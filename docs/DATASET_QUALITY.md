# Historical readiness gate

`validate_dataset(store, market, start, end, asof, calendar=None)` returns an
immutable `DatasetValidationReport` and either a verified `ResearchDataset` or
`None`. Report JSON includes a deterministic SHA-256, exact interval, as-of,
calendar source/version/coverage, snapshot IDs, dataset digest, issue codes,
`missing_windows` and `invalid_windows`. The dataset pins the report JSON for
later simulations. Missing windows form a bounded **backfill plan**; this API
never sends network requests, synthesizes bars or overwrites conflicting data.

```bash
python -m metatr.dataset_cli --store data/raw/futures.sqlite \
  --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --start-utc 2026-10-16T00:00:00Z --end-utc 2026-10-17T00:00:00Z \
  --asof-utc 2026-10-17T00:05:00Z
```

Exit 0 means ready for the specified historical interval; exit 1 blocks use.
`--require-fresh` additionally blocks when the as-of time is more than two
scheduled bars after the range end. Historical interval checks do not equate an
old, explicitly selected research window with a live feed outage.

`FuturesSQLiteStore.read_bars` preserves `limit=N`. Supplying `start_utc`,
`end_utc`, `asof_utc` switches to bounded mode: all three are required, **[start,end)**
uses UTC candle opens, end must be closed by as-of, max 100,000 time slots.
Limit never silently truncates bounded reads. Only snapshots received by as-of
are visible. Full readiness additionally requires the quality gate.

The gate joins batches into one interval and compares all available overlapping
raw candle captures with stored OHLCV, including later captures. It checks raw
checksums/IDs, product identity, timestamp duplicates, missing raw provenance and
critical contract metadata disagreement. Unknown provenance is rejected. The
existing v1 schema and stored raw payloads remain unchanged. Source revisions
still require manual review; do not bypass save_snapshot's conflict rejection.

## Session calendars

No built-in Coinbase calendar is guessed. With unknown calendars, every gap is
`UNVERIFIED_GAP`, which blocks simulations. A contiguous interval may pass without
asserting any knowledge about scheduled closures outside that interval.

An operator can supply **sourced, exact-contract** closure windows using
`--calendar path.json`. `SessionCalendar` pins market, calendar ID/version,
source, IANA timezone, UTC effective coverage and explicit UTC [start,end)
closures. JSON uses the full five-part market string and `closures` as pairs of
UTC timestamps. All boundaries must align to the market timeframe. The source
must actually support these windows; a source field alone does not independently
verify an exchange's calendar. Fixtures explicitly use synthetic test sources.

Unexpected data during a sourced closure is invalid; missing scheduled trading
bars remain missing. Wrong contract/timeframe, overlapping windows, missing
source or coverage expiry fail. BIP/BIT boundaries are never stitched together.
Use the public capture flow to collect each missing window where supported;
conflicts remain errors. Automated historical API backfill is intentionally
optional and not implemented here.
