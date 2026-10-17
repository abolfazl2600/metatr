# Native MT5 external futures viewer — pending native acceptance

`mql5/Experts/MetatrFuturesViewer.mq5` is a separate timer-driven viewer of the
v1 outbound protocol. Native acceptance is in progress on the user's Mac/Wine
installation. **Native acceptance remains incomplete.** PR #35 was merged on 2026-10-17
after the operator explicitly deferred remaining manual checks. See
[PIPELINE_HANDOFF.md](PIPELINE_HANDOFF.md) for the continuation and evidence status. Python source/fixture checks are not
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
was reported to compile without errors; restart/closed-chart acceptance remains deferred. Changed-generation
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

## v1.02 simulated Paper tooltip and banner labeling (native check pending)

A Python-published Paper marker now carries an explicit
`PAPER_SIMULATED_SIGNAL_NOT_ORDER` or `PAPER_SIMULATED_RISK_VETO_NO_ORDER`
reason. Viewer v1.02 displays `PAPER SIMULATED SIGNAL / NOT ORDER` in its
status banner and marks the signal tooltip as **simulated research, NOT an order**.
A risk-vetoed decision appears as `PAPER NO_TRADE`. The same manifest strict
hash checks, custom symbol's trade-mode DISABLED and the no-trade API boundary
remain unchanged. This is distinct from ordinary research signals.

The v1.02 MQL5 modification has **not** been compiled in the operator's native
MetaEditor/Wine installation. Python static and protocol tests cannot claim that
native compilation or visualization acceptance has passed (#20/#21).


## v1.03: data-derived vertical scale for external BTC candles (operator validation pending)

The operator's MT5 screenshots showed ~80k-valued verified Coinbase BTC candles
flattened against a chart price axis extending below zero and above 170k,
including after unchecking the manual Scale Fix and Bid/Ask/Last price lines.
This is a **custom-symbol chart display problem**, not evidence of incorrect
OHLC provenance. The exact reason MT5 selected that automatic range remains
unproven; the zero custom-symbol quote is a plausible contributor.

The viewer now defaults to `AutoFitExternalPrices=true` and
`FitRecentBars=80` (allowed range 16–500) and only **after validating the
whole manifest, source hashes, bar identities, timestamps, and signal pins**:

- Selects Japanese candlesticks on the external chart.
- Hides the external custom symbol's zero Bid, Ask and Last guide lines.
- Calculates the lowest Low and highest High of the last 80 **verified**
  candles, adds 8% range padding or at least 0.05% of the highest price,
  then updates the display-only `CHART_SCALEFIX`,
  `CHART_FIXED_MIN`, and `CHART_FIXED_MAX` properties.
- Reapplies those safe chart properties on repeated, identical-generation
  refreshes, and recomputes them whenever a newer valid publication arrives.
- Refuses nonpositive, inverted or excessively large axis bounds. It never
  modifies, interpolates or synthesizes Coinbase candles or tick quotes;
  the custom symbol remains trade-disabled and no broker orders exist.

To opt out and retain manual chart scaling, set
`AutoFitExternalPrices=false` in the **viewer input settings**, not in
market data, then adjust the chart properties yourself. The viewer can still
force candlestick mode while attached; this applies only to the external
chart it owns/reuses, not the operator's broker chart.

### Native Mac/Wine acceptance checklist

1. In your repository run `git switch main && git pull origin main`.
2. In MT5 choose **File > Open Data Folder**. Copy
   `mql5/Experts/MetatrFuturesViewer.mq5` from the repository into
   the terminal's **MQL5/Experts** directory.
3. Open this source in **MetaEditor**, compile (F7), and capture its build
   errors/warnings plus build/version. **Python CI cannot compile this MQL5 file**.
4. Remove/reattach the updated viewer EA on its existing host chart, keeping
   `CommonSubfolder=metatr\\28c5db082e2e787e` in the *input UI*, and
   `AutoFitExternalPrices=true`. Keep/retain the existing
   `EXT_BIP_28c5db08,M15` display chart (close any old duplicate tabs).
5. With a **fresh, valid** public or explicit fixture publication, the external
   M15 chart should show distinguishable price candlesticks around their OHLC
   range without requiring manual Scale Fix; the signal marker and banner must
   still show `execution=DISABLED`.
6. Check repeated same-generation polling, publish a new valid candle
   generation, restart MT5, and confirm the chart stays reasonably scaled
   without opening duplicates. Collect screenshot and Experts log.
7. An old `VALID` record can age into `STALE/INVALID`. Do not spoof a
   timestamp or bypass stale checks merely to exercise price scaling.

**Status:** implementation merged only after Python CI; real MetaEditor
compilation, native axis appearance and multi-generation refresh still require
explicit operator acceptance under #20/#21 and chart issue #48.
