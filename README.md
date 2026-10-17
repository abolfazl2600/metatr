# metatr — MT5 Signal Intelligence (V1 foundation)

پروژهٔ متن‌باز برای توسعهٔ یک سیستم تحلیل بازار و تولید سیگنال با **Python + MetaTrader 5 + VS Code + GitHub**.

> **وضعیت فعلی: V1 / نسخهٔ آزمایشی فقط‌خواندنی.** هیچ کد ارسال سفارش یا اجرای معامله وجود ندارد. خروجی SAMPLE مصنوعی است؛ سیگنال‌ها توصیهٔ سرمایه‌گذاری نیستند.

## قابلیت‌های پیاده‌سازی‌شده
- منبع SAMPLE مصنوعی و تکرارپذیر، CSV و دریافت کندل‌های **بسته‌شده** از ترمینال MT5 روی ویندوز.
- اعتبارسنجی زمان، OHLC و داده‌های تکراری.
- نمونهٔ استراتژی تقاطع EMA (صرفاً تست زیرساخت، بدون ادعای سودآوری).
- خروجی JSON، خط فرمان، تست آفلاین و GitHub Actions.
- ساختار قابل توسعه برای تحلیل تکنیکال، هوش مصنوعی، بک‌تست و مدیریت ریسک.

## نصب و اجرای آفلاین روی Mac M2
```bash
git clone https://github.com/abolfazl2600/metatr.git
cd metatr
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
python -m metatr.cli --source sample --symbol EURUSD --timeframe M15
```

برای VS Code: پوشهٔ `metatr` را باز کنید؛ Command Palette → **Python: Select Interpreter** → `.venv`.

## استفاده از CSV
CSV باید ستون‌های `timestamp,open,high,low,close` داشته باشد؛ `timestamp` ترجیحاً با پسوند Z یا offset زمانی باشد. کندل‌های CSV باید **کامل و بسته‌شده** باشند.
```bash
python -m metatr.cli --source csv --csv /path/to/candles.csv --symbol EURUSD --timeframe M15
```

## اتصال واقعی به متاتریدر ۵: فقط ویندوز x64
نصب MetaTrader 5 روی macOS برای مشاهدهٔ نمودار ممکن است؛ اما ماژول رسمی `MetaTrader5` پایتون، اتصال بومی روی macOS Apple Silicon را پشتیبانی نمی‌کند. برای اتصال مستقیم Python↔MT5، **Python و ترمینال MT5 باید روی Windows x64 اجرا شوند**.

```powershell
git clone https://github.com/abolfazl2600/metatr.git
cd metatr
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[mt5,dev]"
python -m pytest -q
python -m metatr.cli --source mt5 --symbol EURUSD --timeframe M15 --count 300
```
قبل از فرمان آخر، ترمینال MT5 را اجرا و **حساب دمو** را متصل کنید. برای کار از مک می‌توانید از VS Code Remote SSH به ویندوز متصل شوید. سرویس شبکه‌ای/Bridge امن یک قابلیت آینده است و **در این نسخه پیاده‌سازی نشده**.

## مستندات
- **[گزارش کامل مراحل انجام‌شده روی مک، مسیر فولدرهای MetaTrader/Wine و دستورات ترمینال (۱۷ اکتبر ۲۰۲۶)](docs/LOCAL_MAC_MT5_PROGRESS_FA.md)**
- [نصب و توسعه روی مک](docs/SETUP_MAC.md)
- [اتصال ویندوز/MT5](docs/MT5_WINDOWS.md)
- [معماری](docs/ARCHITECTURE.md)
- [نقشهٔ راه هفت‌نسخه‌ای](docs/ROADMAP.md)
- [قواعد امنیتی](docs/SECURITY.md)

## محدودیت‌های V1
استراتژی EMA فقط یک نمونهٔ آموزشی است؛ بک‌تست معتبر، اجرای زنده، تخمین هزینه‌ها، احتمال کالیبره، اخبار، ML و شبکهٔ سفارشات هنوز پیاده نشده‌اند. خروجی `NO_TRADE` هنگام نبود تقاطع تولید می‌شود. خروجی CSV / MT5 فقط زمانی معتبر است که منبع داده و کندل‌ها درست باشند.

## مراجع رسمی
- [MetaTrader 5 Python integration](https://www.mql5.com/en/docs/python_metatrader5)
- [MT5 copy_rates_from_pos](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py)
- [VS Code Remote SSH](https://code.visualstudio.com/docs/remote/ssh)
- [MetaTrader5 on PyPI](https://pypi.org/project/MetaTrader5/)


## اتصال MT5 نصب‌شده روی macOS از طریق MQL5 Bridge (جدید)
مسیر بدون Windows برای دریافت **کندل‌های بسته‌شده**: Expert Advisor فقط‌خواندنی داخل ترمینال MT5 در Wine، فایل CSV مشترک می‌سازد و Python آن را تحلیل می‌کند. اجرای سفارش ندارد.

**راهنمای نصب گام‌به‌گام:** [docs/MAC_BRIDGE.md](docs/MAC_BRIDGE.md)

**کد Expert Advisor:** [mql5/Experts/MetatrCSVBridge.mq5](mql5/Experts/MetatrCSVBridge.mq5)

پس از نصب EA و پیدا کردن مسیر CSV:
```bash
python -m metatr.bridge --file "/absolute/path/to/metatr_EURUSD_M15.csv" --symbol EURUSD --timeframe M15 --watch
```

کامپایل و اجرای EA روی Wine مک و خواندن CSV در Python روی دستگاه کاربر تأیید شده است. این مسیر از **پکیج رسمی MetaTrader5 Python** استفاده نمی‌کند؛ محدودیت ویندوزِ آن پکیج همچنان برقرار است.


## Futures multi-market research foundation — Coinbase BTC Futures

New read-only modules: `metatr.exchanges.coinbase_futures`, `metatr.futures_core`, and `metatr.futures_cli`.
They discover **actual Bitcoin FUTURE** products (no silent BTC-USD spot fallback), validate completed candles, and select between independent research baseline strategies using the market-specific profiles in `config/markets.json`. Every exchange, contract and timeframe may have its **own** profile and candidate models.

```bash
git pull origin main
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
python -m metatr.futures_cli --list
# Pick an actual returned BTC futures product_id; this placeholder is NOT a symbol:
python -m metatr.futures_cli --product-id ACTUAL_FUTURES_PRODUCT_ID --timeframe M15 --count 300
```

Read [docs/FUTURES_ARCHITECTURE.md](docs/FUTURES_ARCHITECTURE.md) before using this module. **No real orders, account authentication, streaming service, MT5 futures importer or trained AI model exists yet**; output only provides research signals as JSON. Real Coinbase accessibility from your region still needs to be verified.


## Versioned SQLite futures research store (V2 foundation)

`metatr.futures_store.FuturesSQLiteStore` is a local, read-only-data persistence
layer for verified futures candles, immutable raw API snapshots, associated
contract metadata, and schema version 1. It uses standard-library SQLite and
requires no additional service. Data conflicts, malformed timestamps, stale
(open) candles and unknown schema versions fail safely. It has **no orders**,
private API access or autonomous collection loop.

Usage, a precise data contract and migration limitations:
[docs/FUTURES_STORAGE.md](docs/FUTURES_STORAGE.md).

The live-probe CLI can persist the exact public response, contract metadata and
verified bars atomically with `--probe-candles --store data/raw/futures.sqlite`.
Replay stored signals without API requests using [offline research](docs/RESEARCH.md).


## Immutable research Strategy & Model Registry (Stage 1/4)

`metatr.model_registry.StrategyRegistry` stores pinned, immutable versions of
allowlisted strategies by exact `(exchange, asset, instrument, product_id, timeframe)`
in **separate** `data/research/registry.sqlite` (schema v1). It never modifies the
existing futures data DB, enables order submission, or changes the legacy futures CLI.

```bash
python -m pip install -e ".[dev]"
python -m metatr.futures_cli --list  # verify an actual native futures product first
metatr-registry import-legacy --exchange coinbase --asset BTC --instrument FUTURE \
  --product-id BIP-20DEC30-CDE --timeframe M15 --allow-wildcard
metatr-registry list
```

Legacy wildcard profiles require explicit `--allow-wildcard` and materialize a
**real exact market**, never `*`. All returned specs state `execution=DISABLED`;
`draft` / `research` / `retired` statuses do not authorize trading. See
[docs/MODEL_REGISTRY.md](docs/MODEL_REGISTRY.md) for SQL, lifecycle, API and CLI.

Pure pinned research adapters and the developer extension contract are documented in
[docs/STRATEGIES.md](docs/STRATEGIES.md). Legacy futures selection remains compatible.

Historical readiness, as-of reads, sourced session calendars and offline backfill
planning are documented in [docs/DATASET_QUALITY.md](docs/DATASET_QUALITY.md).

Exact-decimal futures simulation specifications, assumed costs and unsupported
margin/funding limits: [docs/CONTRACT_ECONOMICS.md](docs/CONTRACT_ECONOMICS.md).

The deterministic, next-bar hypothetical futures simulator and its accounting/event
policies are documented in [docs/BACKTEST.md](docs/BACKTEST.md).

Purged chronological train/validation/test folds and frozen candidate selection:
[docs/VALIDATION.md](docs/VALIDATION.md).

Auditable offline reports, net-equity metrics, OOS cost stress and immutable JSON/CSV
exports: [docs/BACKTEST_REPORTS.md](docs/BACKTEST_REPORTS.md). Positive backtest
results cannot guarantee live profitability.

One-way external futures display publishing and a synthetic offline fixture command:
[docs/MT5_OUTBOUND.md](docs/MT5_OUTBOUND.md). Actual MT5/Wine rendering requires
separate native viewer compilation and manual acceptance.

Isolated simulated accounts and append-only paper ledger foundations:
[docs/PAPER_LEDGER.md](docs/PAPER_LEDGER.md). Nothing starts on import; real trading
is outside this subsystem.

Paper risk limits, explicit hypothetical collateral assumptions and persistent
kill-switch/reset rules: [docs/PAPER_RISK.md](docs/PAPER_RISK.md).

Risk-approved next-bar paper fills and conservative restart/reconciliation behavior:
[docs/PAPER_SIMULATOR.md](docs/PAPER_SIMULATOR.md).

Explicit opt-in paper runner, offline replay, health monitoring, restart recovery,
status/audit exports and Mac operator runbook:
[docs/PAPER_OPERATIONS.md](docs/PAPER_OPERATIONS.md). The native #20/#21 visual
acceptance and monitored paper soak are separate pending operational sign-offs;
unit tests do not establish them. No real execution option exists.

## Integrated external display handoff

[SQLite → research → MT5 operator command and evidence](docs/PIPELINE_HANDOFF.md)
provides an offline fixture harness and a separate opt-in public Coinbase capture.
Native visual acceptance remains pending; execution is disabled.

## شروع سریع Paper روی مک

[فرمان راه‌اندازی و تنظیمات سناریوی Paper](docs/PAPER_QUICKSTART_FA.md):
ساخت خودکار حساب فرضی، دریافت مشخصات بومی و جلسهٔ باز از API و شروع polling.
معاملهٔ واقعی غیرفعال است؛ تأیید پایش عملیاتی همچنان جداست.
