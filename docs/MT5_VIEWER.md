# Native MT5 external futures viewer — pending native acceptance

`mql5/Experts/MetatrFuturesViewer.mq5` is a separate timer-driven viewer of the
v1 outbound protocol. Native acceptance is in progress on the user's Mac/Wine
installation. **Do not mark #20 complete or merge its PR until the native build
and manual checklist below are recorded.** Python source/fixture checks are not
native compilation or visual integration proof.

Observed on 2026-10-17 for PR #35's original head `49abad7`:
- MetaEditor screenshot: 0 errors, 0 warnings, 2076 ms, X64 Regular.
- MT5 screenshot/log: `EXT_BIP_28c5db08,M15`, 80 bars and 80 signals,
  `EXTERNAL_VALID`, `execution=DISABLED`, synthetic fixture; visible arrows and NO_TRADE.
- Repeated same-generation publication preserved one tab before restart.
- User reports successful terminal restart. Subsequent screenshot revealed a second
  custom-symbol chart: the original version did not rediscover retained chart IDs.
- Missing manifest drill: screenshot shows prior candles retained and visible
  `STALE/INVALID`; user reports restoration returned to VALID.

Version 1.01 reuses an existing matching M15 chart on initialization and before
opening a chart, and recreates a manually closed display on the next verified poll.
It leaves any pre-existing duplicate tabs for the user to close. This revision
still needs native recompilation and restart/closed-chart acceptance. Changed-generation
updates, exact CSV/candle comparisons, remaining failure drills and build details
also remain pending; no complete native sign-off is claimed.

1. Install the EA in the terminal's `MQL5/Experts`, open it in MetaEditor, compile,
   and save the exact build log (errors/warnings, terminal build, timestamp).
2. Publish the synthetic fixture using [MT5_OUTBOUND.md](MT5_OUTBOUND.md). Set
   `CommonSubfolder` to `metatr\<16-character-directory>` printed by the publisher,
   relative to FILE_COMMON, and exact expected product/model/version inputs.
3. Attach the viewer to a spare chart. Its timer opens a distinct `EXT_BIP_...`
   custom-symbol M15 chart with external/simulated labels and disabled trading.
   Keep the viewer host chart open. AutoTrading is not required for order routing;
   this viewer contains no order calls, account lookups or trade classes.
4. Compare the printed product, source run ID, row count, latest UTC time and model
   with the manifest, then compare OHLC and LONG/SHORT/NO_TRADE markers by candle
   open time. Decision time is the close of that candle. Unix seconds are imported
   **unchanged as UTC**, with no broker/server timezone offset; chart timestamps
   intentionally represent external UTC rather than broker-local candles.
5. Publish a new fixture, repeat the same run, restart the terminal, and perform
   failure drills: missing/partial payload, wrong product, corrupt hash, wrong
   schema, stale timestamp and interrupted manifest rename. A failure must retain
   prior chart data with STALE/INVALID banner and no newly actionable marker.

The native custom-history API stores **M1 anchors**. Each verified M15 candle is
stored at its original UTC open without inventing fourteen intermediate minute
candles. Open the intended **M15 chart only**; the sparse underlying M1 cache
is not a true one-minute feed. Native volume fields are integers, so display
volume is source units × 1e8, explicitly labeled. Original source precision remains
in Python/CSV. This is external research data, never an executable broker market.

The viewer validates byte counts/SHA-256 before parsing, exact schema/identity,
UTC order/alignment/closed age, OHLCV, counts and signal/model/data pins before
updating a custom symbol. Reads, bars and chart objects are bounded; run IDs are
deduplicated; timer cleanup removes only viewer-owned objects. Unknown or invalid
inputs show a visible error. It does not independently recalculate strategies.

Required evidence: native compile log; screenshot/observation of exact custom
symbol and candle count/time; matching model/action/run ID; repeated update and
failure-drill results. Record the Mac/Wine/MT5 build and any native API limitations.
Until recorded, #20 remains pending and #21's native pipeline exit gate is blocked.
The independent Python paper subsystem does not depend on visual acceptance.

Official API references used for implementation:
- https://www.mql5.com/en/docs/common/cryptencode
- https://www.mql5.com/en/docs/files/filereadarray
- https://www.mql5.com/en/docs/customsymbols/customratesreplace
- https://www.metatrader5.com/en/terminal/help/trading_advanced/custom_instruments
