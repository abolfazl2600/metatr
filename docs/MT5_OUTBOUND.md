# One-way Python → MT5 external futures display protocol v1

This outbound path is independent of the existing terminal→Python EURUSD CSV
bridge. Python remains the source of truth. No MT5 Python bindings, trading API,
account authentication or order calls are used. The UI label is always
`EXTERNAL_SIMULATED_DATA`, `execution=DISABLED`; even actual Coinbase market data
is an external research visualization, never a broker-executable symbol.

## Location and publication

Find the Wine prefix's `drive_c/users/<wine-user>/AppData/Roaming/MetaQuotes/Terminal/Common/Files`
folder. In MT5, **File → Open Data Folder** helps locate the terminal prefix;
Common/Files is shared across terminals, not that terminal's own MQL5/Files.
Pass its absolute macOS path as `--output-dir`. Files live beneath
`metatr/<first-16-SHA256-of-canonical-market-string>/` with no unsanitized market
path components. The CLI prints the exact manifest path.

```bash
python -m metatr.mt5_publish_cli --fixture \
  --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --output-dir /absolute/path/to/Terminal/Common/Files
```

This command is explicitly **synthetic**, seeds temporary raw/store/registry
fixtures and publishes 80 recent synthetic closed bars. It makes no network
requests and never labels the fixture as actual market prices. `--now-utc` pins
reproducible fixture time. For actual data omit `--fixture` and provide `--store`,
`--registry`, `--strategy-id`, `--strategy-version`, `--start-utc`, `--end-utc`,
`--asof-utc`, `--calendar`, and `--contract-spec`, using the same exact sourced
inputs as the research/backtest CLIs. Source/contract/session verification and
fresh closed bars are mandatory. Stale data or identity mixing blocks publication.

Immutable bars/signals are flushed/fsynced and atomically renamed in the same
directory. A single-row `manifest.csv` is atomically replaced **last** as commit
marker. Native CSV readers must read one manifest then only the named immutable
payloads, validate everything, and only then update the chart. Another publication
cannot mix its bars with the prior manifest's signals. The lock directory enforces
one publisher. A crash leaves `writer.lock`; inspect logs and confirm no writer
is alive before manually removing that directory. Never remove another live lock.

At least two generations are retained (default three). Cleanup only follows a
validated commit. A very slow reader can encounter a retired payload and must
keep its prior chart visibly STALE/INVALID, then reread a fresh manifest. Never
reuse another run's similarly named file or display a stale signal as current.

## Exact file schemas

Files are ASCII, comma-delimited, LF-terminated, with one header. Free-form
reasons are sanitized to ASCII without commas/newlines/quotes for native MQL5.
UTC timestamps use integer Unix seconds, never local broker wall time.

| File | Columns in order |
| --- | --- |
| `bars_<run_id>.csv` | timestamp, open, high, low, close, volume, product_id, timeframe |
| `signals_<run_id>.csv` | bar_open_utc, decision_utc, action, strategy_id, strategy_version, config_sha256, reason, data_sha256 |
| `manifest.csv` | schema_version, market, source, capture_utc, latest_bar_utc, bar_count, signal_count, bars_file, bars_bytes, bars_sha256, signals_file, signals_bytes, signals_sha256, run_id, data_sha256, contract_sha256, strategy_id, strategy_version, config_sha256, status, execution, label |

Manifest schema is string `1`. Full Market includes exchange:asset:instrument:
product_id:timeframe. Run ID pins canonical manifest identity and payload hashes;
SHA-256 is over exact payload bytes. Counts include data rows only. Signals mark
the closed candle's **open** time, while `decision_utc` is its close time; do not
shift decisions backward into the candle's formation. Only LONG/SHORT/NO_TRADE
are accepted. Unknown schema/market, bad checksum/byte count/row count, missing
file, unordered/duplicate/malformed/unclosed timestamps, wrong product/timeframe,
wrong model/data digests or stale data require STALE/INVALID without a fresh arrow.
A missing latest decision or unknown action publishes explicit INVALID/NO_TRADE
rather than retaining an earlier actionable signal.

The Python reference `read_publication` validates protocol fidelity. Python CI
covers interrupted commit, corruption, freshness, idempotence, concurrent reader
and bounded retention. Actual native MetaEditor compile and MT5/Wine rendering
remain separate manual acceptance steps for the viewer issue.
