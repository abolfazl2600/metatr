# BTC Futures — Backtest results

ثبت آزمایش‌های TradingView برای مقایسه کانفیگ‌ها. نتیجه‌ها فقط از بک‌تست یا شواهد واقعی کاربر ثبت می‌شوند؛ مقدارهای نامعلوم با «تأیید نشده» مشخص می‌شوند.

## Baseline — strategy.pine v1.7

| Setting | Default |
| --- | --- |
| Symbol | BTC linear USD/USDT perpetual — specify exact TradingView symbol |
| Execution chart | 5m, standard candles (required for true 5m exit) |
| Entry timeframe | 15m only, at confirmed close; EMA, RSI, Donchian, spike, RVOL and 24h range in 15m context |
| Confirmed trend | Monthly EMA 6/12 + Weekly EMA 10/30 (both aligned) |
| Entry signal | EMA crossover 21/55; RSI 14; Long RSI ≥52, Short RSI ≤48 |
| Alternative signal | Donchian breakout 20 |
| Long/Short leverage | 1x / 1x |
| Equity allocation | 95% |
| TP/SL (price move) | 0.5% / 1.0% |
| Profitable opposite 5m candle exit | Enabled; opposite 5m close AND strategy.openprofit > 0 (gross unrealized PnL) |
| 24h high/low proximity block | Enabled; no new entry when confirmed 15m close is within 1.0% of either rolling 24h extreme (96 confirmed bars, including signal bar) |
| Volume Spike | Enabled; at most 3 × SMA(volume, 20)[1] |
| RVOL | Enabled; minimum 0.8; 14 prior same-time daily slots |
| Estimated Delta | Enabled; minimum 10 one-minute candles; 80% volume coverage |
| Initial capital | 100 USD |
| Commission / side | 0.05% |
| Slippage | 2 ticks |
| Bar Magnifier | Record whether enabled |
| Backtest date interval | Record UTC range |

## Experiment log

| Run ID | Code version | Symbol/exchange | Backtest period | Config changes | Trades | Win rate % | Net profit % | Profit factor | Max drawdown % | Avg trade | Evidence / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BT-001 | v1.5 | Binance BTC/USDT perpetual (exact ticker unverified) | 2026-08-01 to 2026-10-18 (date selector; timezone unverified) | 15m EMA cross; equity 95%; L 3x / S 1x; TP 1.0%, SL 1.0%; opposite-candle exit ON; monthly EMA 6/12, weekly EMA 10/30; **no 24h filter in v1.5** | 21 | 33.33 | +0.43 | 1.381 | 0.89 | Not reported | User screenshot (2026-10-18); partial config; see details below |
| — | v1.7 | — | — | Baseline, execution 5m / entry M15, 1x/1x, profitable 5m reverse-candle exit ON | — | — | — | — | — | — | Not yet tested |

## BT-001 — Screenshot-derived details (2026-10-18)

- **Version:** `BTC Futures Bot | FA | v1.5` (visible both in chart legend and Strategy Tester). This is **not** the newer v1.6 filter evaluation.
- **Market:** TradingView displays *Bitcoin / TetherUS PERPETUAL CONTRACT · 15 · Binance*; exact exchange symbol ID and whether standard candles were used were not supplied explicitly.
- **Selected testing dates:** Aug 1, 2026 — Oct 18, 2026; date-selector timezone is not shown.
- **Visible settings:** EMA crossover, enable Long/Short, exit on opposite closed M15 candle enabled, capital allocation 95%, Long leverage 3x, Short leverage 1x, TP 1% of price, SL 1% of price, monthly EMA 6/12 and weekly EMA 10/30. Other filters' enabled/disabled states, Strategy Properties overrides, and whether edits in the open Inputs dialog have been applied are **not independently verified**.
- **Observed outputs:** initial capital 100 USD; net PnL +0.43 USD (+0.43%); gross profit +1.57 USD; gross loss 1.14 USD; 7 winning / 21 total closed trades (33.33%); Profit Factor 1.381; max drawdown 0.90 USD (0.89%); Buy & Hold +22.98% as displayed.
- **Not captured:** List of Trades, individual entry/exit reasons, full active-filter states, Commission/Slippage from Properties, Bar Magnifier, average trade, out-of-sample evaluation. Do **not** treat these unknowns as confirmed defaults.
- **Comparison warning:** BT-001 uses different short leverage and TP than v1.5/v1.6 defaults. To isolate the new 24h exclusion filter, hold *all other Inputs and Properties* identical between v1.6-filter-ON and v1.6-filter-OFF trials.

## Reporting format

برای هر اجرای بک‌تست یک Run ID بدهید و این داده‌ها را بفرستید. **از v1.7 به بعد حتماً چارت 5m بگذارید، سیگنال ورود همچنان M15 است و خروج با کندل مخالف فقط در صورت سود باز مثبت انجام می‌شود. نتایج نسخه‌های v1.5/v1.6 با اجرا روی 15m را بدون ذکر تفاوت تایم‌فریم با v1.7 مستقیم مقایسه نکنید.** برای ارزیابی اثر فیلتر جدید، یک آزمایش با **24h proximity ON / 1.0%** و یک آزمایش با **OFF**، روی همان نماد، بازه و سایر تنظیمات اجرا کنید:


- نماد دقیق، صرافی، تاریخ آغاز و پایان و تایم‌فریم، نوع نمودار (کندل استاندارد یا غیره).
- نسخهٔ کد، مدل سیگنال، فیلترهای روشن/خاموش، پارامترهای تغییرکرده، اهرم/سرمایه و TP/SL.
- Strategy Properties: initial capital، order size، commission، slippage، و Bar Magnifier / fill settings.
- نتایج: تعداد معاملات بسته‌شده، درصد برد، سود/زیان خالص (USD و %)، Profit Factor، Max Drawdown، Avg trade و در صورت امکان آمار Long/Short.
- اسکرین‌شات Overview، Performance Summary، List of Trades یا خروجی CSV؛ مشخص کنید اطلاعات In-sample یا Out-of-sample است.

برای جلوگیری از overfitting، ابتدا آزمایش‌ها را روی دوره ثابت انجام دهید، هر بار فقط یک گروه پارامتر را تغییر دهید و سپس روی دوره خارج از نمونه اعتبارسنجی کنید.
