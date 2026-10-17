# 7-Version Product Roadmap

هدف: ساخت یک پلتفرم توسعه‌پذیر تحلیل و تولید سیگنال برای MT5، با اولویت **صحت داده، اعتبارسنجی و مدیریت ریسک**.

## V1 — Foundation & read-only integration (current starter)
**داخل محدوده:** ساختار پروژه، GitHub + CI، تنظیمات VS Code، sample/CSV/MT5 read-only، استاندارد UTC، حذف کندل در حال تشکیل، اعتبارسنجی داده، قرارداد JSON، تست آفلاین، یک EMA reference strategy.
**خارج از محدوده:** سفارش واقعی، AI، بک‌تست پرتفوی، استراتژی سودآور تضمینی، سرویس دائمی و Bridge شبکه‌ای.
**معیار پذیرش:** اجرای تست آفلاین روی macOS/Linux، اجرای sample، و بررسی دستی اتصال به ترمینال دمو روی Windows.

## V2 — Technical research & historical store
**داخل:** لایه ingest پایدار، ذخیره تاریخچه OHLCV و تیک در صورت دسترسی، شاخص‌های EMA/RSI/ATR/MACD، حمایت و مقاومت قابل تعریف، تشخیص چند تایم‌فریم، Feature Registry، آزمون واحد.
**خارج:** معاملات خودکار و استفاده از شاخص‌های اثبات‌نشده به‌عنوان تصمیم قطعی.
**پذیرش:** تکرارپذیری کامل محاسبات و کنترل کندل بسته‌شده / منطقه زمانی.

## V3 — Backtest & experiment management
**داخل:** بک‌تست event-driven، کمیسیون، اسپرد، لغزش و محدودیت نقدینگی، Walk-Forward، آزمون خارج از نمونه، گزارش Sharpe/Sortino/Max Drawdown، کنترل نشت اطلاعات.
**خارج:** بهینه‌سازی صدها پارامتر بدون کنترل multiple testing و ادعای سود تضمینی.
**پذیرش:** قابل بازتولید بودن نتایج و مقایسه با baseline ساده پس از هزینه‌ها.

## V4 — Statistical & machine learning intelligence
**داخل:** LightGBM/XGBoost baseline، طبقه‌بندی رژیم بازار، مدل‌های سری زمانی، Feature Importance، Calibration و زمان‌سنجی inference، Model Registry.
**خارج:** آموزش با داده آینده، استفاده از accuracy تنها به‌عنوان معیار موفقیت و RL بدون شبیه‌ساز معتبر.
**پذیرش:** آزمون purged/walk-forward در صورت نیاز و نشان‌دادن ارزش افزوده نسبت به baseline.

## V5 — Multi-source market intelligence
**داخل:** در صورت وجود داده معتبر: Order Flow، مشتقات، اخبار، اقتصاد کلان، و برای کریپتو آن‌چین؛ schema، timestamp provenance، کیفیت منبع و وضعیت تأخیر.
**خارج:** فرض کردن وجود L2 حقیقی در تمام بروکرهای MT5؛ تصمیم‌گیری بر مبنای داده تخمینی بدون برچسب.
**پذیرش:** قابلیت قطع امن هر منبع ناقص و ثبت علت.

## V6 — Ensemble, explainability & risk dashboard
**داخل:** وزن‌دهی پویا، Meta Learner، NO_TRADE veto، تعیین horizon و expiry، مدل تخمین ریسک، توضیح سیگنال، API و داشبورد.
**خارج:** الزام به صدور BUY/SELL برای هر کندل، استفاده از احتمال کالیبره‌نشده به‌عنوان اعتماد واقعی.
**پذیرش:** ثبت نسخه مدل و داده، دلایل تصمیم و نتایج واقعی/آزمایشی قابل ممیزی.

## V7 — Paper automation & gated execution
**داخل:** paper trading، کنترل ریسک حساب و پرتفوی، بروکر adapter ایزوله، مدیریت retry/idempotency، مجوز صریح برای اجرای واقعی، kill switch، لاگ و مانیتورینگ.
**خارج:** اجرای خودکار پول واقعی به‌صورت پیش‌فرض و قابلیت دور زدن سقف زیان.
**پذیرش:** آزمایش طولانی روی دمو، توقف امن هنگام خطا، تست بازیابی و تأیید جداگانه پیش از فعال‌سازی live.

## قواعد بین نسخه‌ها
- هر قابلیت باید تست، راهنما و معیار پذیرش داشته باشد.
- داده‌های زنده/شخصی خارج از GitHub ذخیره می‌شوند.
- ترکیب مدل‌ها فقط در صورت بهتر بودن معتبر روی داده خارج از نمونه مجاز است.
- تغییرات هر نسخه با Pull Request و CI بررسی می‌شوند.


## V1 extension — macOS/Wine bridge
MQL5 read-only CSV bridge and Python watcher included; **real-world compilation and E2E integration are pending on macOS Wine**. This is separate from the official Windows Python integration and does not authorize orders.


## Implementation checkpoints — October 2026

The seven product versions above remain the high-level roadmap. Coinbase Futures
is being added incrementally within these versions, without replacing the
existing MT5/macOS bridge or the Python research engine.

| Priority | Delivery gate | Version | Evidence required |
| --- | --- | --- | --- |
| P0 | Native CDE BTC/BCH contract identity and per-product metadata | V1 | Offline regression tests, live metadata consistency |
| P0 | Actual BIP M15 public candles, complete-bar UTC and gap checks | V1/V2 | Live probe log with product, timestamps and validated count |
| P1 | Immutable source snapshots and schema-versioned local store | V2 | Reproducible imports, UTC and provenance tests |
| P1 | Registry/feature interfaces + research experiments | V2/V3 | Versioned config, causal evaluation and OOS validation |
| P1 | Walk-forward + realistic futures-aware backtester | V3 | Funding, fees, contract size, sessions, OOS performance |
| P2 | Python to Wine FILE_COMMON to MT5 custom-symbol display | V5/V6 | End-to-end Mac/Wine manual verification; never place MT5 orders |
| P2 | Paper fills, positions, ledger and risk veto | V7 | Reconciliation and restart tests, kill switch |
| P3 | Authenticated venue-native real orders (separate gated project) | V7+ | Explicit live permission, legal eligibility and audited readiness |

Architecture boundaries:
`Exchange MarketData Adapter → Canonical Identity/Contract Metadata → Bar
Quality Gate → Provenance/Store → Feature/Strategy Registry → Experiment
Evaluator/Model Selector → Risk Veto → Paper/Live Execution Adapter`.
Python owns all computation and order flow. A **one-way display adapter**
publishes verified information to MT5; MT5 is never the authoritative
source of exchange order or position state.

The first Coinbase live smoke is a separate network-only workflow because
a green mock CI must never be interpreted as proof of trading-market access.
Keep `NO_TRADE` and `execution=DISABLED` unless every relevant gate passes.
