# Architecture — هدف بلندمدت

## جریان داده
```text
Market sources (MT5 + future external APIs)
        |
Ingestion + timestamp/data validation
        |
Immutable raw events + curated bar stores
        |
Feature engines: technical / order flow / derivatives / macro / sentiment
        |
Market regime detection + independent predictive models
        |
Ensemble + uncertainty + risk veto
        |
Signal API (BUY / SELL / NO_TRADE, rationale, timestamps)
        |
Paper trading -> gated execution layer (future versions only)
        |
Monitoring, backtests, walk-forward, model lifecycle
```

## مرزبندی V1
- `src/metatr/data.py`: فقط آداپتورهای دریافت داده و اعتبارسنجی.
- `src/metatr/strategy.py`: الگوریتم شفاف EMA و خروجی ساختاریافته.
- `src/metatr/cli.py`: خط فرمان مستقل از بروکر.
- `tests/`: اجرای مستقل از شبکه و حساب واقعی.
- **هیچ متد `order_send`، هیچ دسترسی به رمز حساب، و هیچ معاملهٔ واقعی وجود ندارد.**

## معماری اتصال مک به متاتریدر
ترمینال MT5 روی macOS به معنی دسترسی بومی از پکیج رسمی Python نیست. اجرای مستقیم API رسمی مستلزم محیط Windows x64 با ترمینال باز در همان محیط اجرایی است. توسعه از مک با VS Code؛ اجرای MT5 از Windows Remote Desktop/VS Code Remote SSH. طراحی آداپتور شبکه‌ای با احراز هویت و TLS تنها در نسخه‌های آینده بررسی می‌شود.

## قواعد داده
1. همه زمان‌ها UTC و timezone-aware؛ زمان انتشار و زمان دریافت داده در نسخه‌های پیشرفته جدا می‌شوند.
2. ورودی MT5 با position=1، کندل در حال تشکیل را کنار می‌گذارد.
3. CSV باید شامل فقط کندل‌های بسته‌شده باشد.
4. منبع SAMPLE مصنوعی است و هرگز نباید به‌عنوان دادهٔ بازار واقعی نمایش داده شود.
5. هر استراتژی آینده باید آزمون زمانی خارج از نمونه، هزینه‌ها و کیفیت داده را گزارش کند.

## قرارداد سیگنال V1
`symbol, timeframe, source, timestamp, action, reason, fast_ema, slow_ema, synthetic`.

پروتکل نسخه‌های بعد باید `signal_id, model_version, horizon, market_regime, confidence_calibration, costs, risk, expiry` را اضافه کند.


## V1 optional macOS/Wine file bridge
- MQL5 EA `mql5/Experts/MetatrCSVBridge.mq5` publishes **only closed** OHLC+tick-volume snapshots through `FILE_COMMON`.
- Python `src/metatr/bridge.py` reads the CSV via filesystem; no official `MetaTrader5` Python package is imported.
- Temporary CSV is finalized and renamed with `FileMove`, then the watcher emits one signal per latest-bar timestamp.
- Signal remains read-only. **EA compilation and Wine filesystem behavior are not verified on the user's Mac yet.**
