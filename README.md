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
