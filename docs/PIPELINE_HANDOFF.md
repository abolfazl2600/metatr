# SQLite → Python research → MT5 handoff

Issue #21 implementation is available. **Native end-to-end acceptance remains
pending.** On 2026-10-17 the operator explicitly requested proceeding without
further manual tests; PR #35 was merged with this override. This does not convert
unperformed checks into passes. Version 1.01 compiled without errors according to
the operator. Earlier observations established synthetic candles/signals,
missing-manifest STALE/INVALID, recovery to VALID, and reopening the terminal.
No live Coinbase handoff or complete native visual sign-off is claimed.

## One command on the operator's Mac

From the existing `metatr` checkout with its virtual environment active:

```bash
git switch main
git pull origin main
python3 -m pip install -e .
python3 -m metatr.pipeline_cli --fixture \
  --workspace "$HOME/metatr-state/display-integration" \
  --output-dir "$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
```

This is **80 synthetic M15 candles**, not market prices. It persists validated
source snapshots in SQLite, registers exact EMA and Momentum 1.0.0 models,
replays each using closed-bar history, publishes Momentum to FILE_COMMON, then
independently reads the manifest and compares every timestamp, OHLCV value,
action, decision time and model/data/contract pin with the research inputs.
Use `--strategy-id ema_cross` to publish EMA instead and change the viewer's
ExpectedStrategy input accordingly. Research outputs and content-addressed
handoff evidence are retained beneath the workspace. Evidence is immutable and
always labels native acceptance pending. A fixed `--now-utc 2020-01-02T00:00:00Z`
is for offline CI only; historical output will correctly be STALE in today's MT5.

The viewer must be installed/compiled from `mql5/Experts/MetatrFuturesViewer.mq5`
in the active terminal's MQL5/Experts folder. Attach it to a spare host chart;
keep that chart open. The stable custom symbol is `EXT_BIP_28c5db08` on M15.
Inputs: CommonSubfolder=`metatr\28c5db082e2e787e`, ExpectedProduct=`BIP-20DEC30-CDE`,
ExpectedStrategy=`momentum`, ExpectedVersion=`1.0.0`.
No AutoTrading permission is needed. UTC seconds are unchanged, not converted to
broker time; a marker sits on candle open and its decision time is candle close.

## Optional actual public Coinbase capture

Network access is explicit and separate; fixture mode never calls the feed.
Supply independently sourced, exact ContractSpec and SessionCalendar JSON files
as documented in [CONTRACT_ECONOMICS.md](CONTRACT_ECONOMICS.md) and
[DATASET_QUALITY.md](DATASET_QUALITY.md):

```bash
python3 -m metatr.pipeline_cli --live \
  --workspace "$HOME/metatr-state/public-integration" \
  --contract-spec /absolute/path/to/verified-contract.json \
  --calendar /absolute/path/to/verified-calendar.json \
  --output-dir "/absolute/path/to/Terminal/Common/Files"
```

This captures the exact BIP contract's public M15 candles, records the raw
response and receipt time, validates quality/calendar/contract metadata, and
runs the same handoff. It does not substitute spot or fixture data on failure.
Access restrictions, missing candles or out-of-coverage session pins stop the
run. Neither credentials nor private/order endpoints are used. The chart remains
external research visualization with `execution=DISABLED` even for real prices.

## Deferred native evidence and failure drills

These checks are documented for later use; they do not block development under
the operator's instruction. Keep #20/#21 native acceptance open until witnessed.
Record Mac/Wine/terminal builds, compile output, manifest run ID/product/model,
bar/signal counts, exact UTC/OHLC/action comparisons and Experts/Journal logs.
Save screenshots or an explicit observation checklist beside the handoff JSON.

| Drill | Required native observation |
| --- | --- |
| Changed generation, repeated snapshot, terminal restart | Same symbol/chart reused, updated run ID when data changes, no duplicate markers/tabs |
| Missing/partial payload; malformed timestamp; wrong market/schema; corrupt hash | Previous chart retained, visible INVALID/STALE, no fresh actionable marker |
| Interrupted manifest rename; older manifest with newer unrelated payloads | Old generation remains coherent; ignore unnamed new files |
| Stale source snapshot | STALE, no fresh actionable signal |
| Next valid publication | Recover to VALID on the same chart |

Python CI verifies the store/replay/file handoff and deterministic manifest hash.
Existing publisher tests cover interrupted publication, corruption, stale data,
market/schema mismatch and retention; none prove native Wine chart behavior.
The old terminal→Python EURUSD CSV bridge remains a separate path.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No module named metatr | Activate `.venv` and run `python3 -m pip install -e .` in checkout |
| Files written but no chart | Check FILE_COMMON path versus terminal MQL5/Files; attach viewer to host chart |
| INVALID or STALE | Read Experts/Journal; inspect named manifest generation; republish fresh verified inputs |
| writer.lock exists | Confirm no writer is alive and inspect interrupted publication before manually removing lock |
| Duplicate tabs left from old version | Keep one display tab; version 1.01 reuses a matching retained M15 chart |
| Public capture rejected | Keep error evidence; verify access, product and sourced calendar/contract; never relabel fixture as live |

Next operational step is the independent [Paper runner](PAPER_OPERATIONS.md):
use exact verified contract, cost and risk policies, explicitly enable paper mode,
and record the monitoring period. No real-money execution is implemented here.
