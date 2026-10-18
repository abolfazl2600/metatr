# BTC Futures — Backtest results

ثبت آزمایش‌های TradingView برای مقایسه کانفیگ‌ها. تا این تاریخ هنوز **هیچ نتیجه واقعی** ثبت نشده است.

## Baseline — strategy.pine v1.6

| Setting | Default |
| --- | --- |
| Symbol | BTC linear USD/USDT perpetual — specify exact TradingView symbol |
| Chart | 15m, standard candles |
| Confirmed trend | Monthly EMA 6/12 + Weekly EMA 10/30 (both aligned) |
| Entry signal | EMA crossover 21/55; RSI 14; Long RSI ≥52, Short RSI ≤48 |
| Alternative signal | Donchian breakout 20 |
| Long/Short leverage | 3x / 3x |
| Equity allocation | 95% |
| TP/SL (price move) | 0.5% / 1.0% |
| Opposite 15m candle exit | Enabled |
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
| — | v1.6 | — | — | Baseline, 24h block ON at 1.0% | — | — | — | — | — | — | Not yet tested |

## Reporting format

برای هر اجرای بک‌تست یک Run ID بدهید و این داده‌ها را بفرستید. برای ارزیابی اثر فیلتر جدید، یک آزمایش با **24h proximity ON / 1.0%** و یک آزمایش با **OFF**، روی همان نماد، بازه و سایر تنظیمات اجرا کنید:


- نماد دقیق، صرافی، تاریخ آغاز و پایان و تایم‌فریم، نوع نمودار (کندل استاندارد یا غیره).
- نسخهٔ کد، مدل سیگنال، فیلترهای روشن/خاموش، پارامترهای تغییرکرده، اهرم/سرمایه و TP/SL.
- Strategy Properties: initial capital، order size، commission، slippage، و Bar Magnifier / fill settings.
- نتایج: تعداد معاملات بسته‌شده، درصد برد، سود/زیان خالص (USD و %)، Profit Factor، Max Drawdown، Avg trade و در صورت امکان آمار Long/Short.
- اسکرین‌شات Overview، Performance Summary، List of Trades یا خروجی CSV؛ مشخص کنید اطلاعات In-sample یا Out-of-sample است.

برای جلوگیری از overfitting، ابتدا آزمایش‌ها را روی دوره ثابت انجام دهید، هر بار فقط یک گروه پارامتر را تغییر دهید و سپس روی دوره خارج از نمونه اعتبارسنجی کنید.
