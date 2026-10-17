# اتصال MetaTrader 5 مک M2 به Python با MQL5 FILE_COMMON Bridge

## هدف و محدودیت
این واسط فقط کندل‌های **بسته‌شده** را از ترمینال MT5 نصب‌شده با Wine روی macOS به فایل CSV مشترک صادر می‌کند؛ Python همان فایل را برای نمونه‌استراتژی EMA می‌خواند. **هیچ سفارش معاملاتی ارسال نمی‌شود.** به پکیج رسمی MetaTrader5 Python یا Windows VPS نیاز ندارد.

کامپایل و اجرای واقعی EA در محیط Wine روی مک شما هنوز باید تأیید شود. تست‌های Python آفلاین انجام شده‌اند؛ مسیر فایل میان نسخه‌های MT5/Wine متفاوت است.

## ۱. نصب Expert Advisor در متاتریدر

1. متاتریدر ۵ را روی مک باز کنید، حساب Demo را متصل کرده و یک نمودار مانند **EURUSD / M15** را باز کنید. نام دقیق نماد بروکر ممکن است پسوند داشته باشد.
2. در MT5 گزینه **File > Open Data Folder** را بزنید و وارد پوشه MQL5/Experts شوید.
3. فایل [MetatrCSVBridge.mq5](../mql5/Experts/MetatrCSVBridge.mq5) را از پوشه mql5/Experts پروژه GitHub در آنجا کپی کنید.
4. **MetaEditor** را باز کنید (معمولاً F4)، فایل را باز کنید و **Compile (F7)** را اجرا کنید. نباید خطای کامپایل وجود داشته باشد.
5. از Navigator > Expert Advisors، لیست را Refresh کنید و Expert Advisor را روی نمودار بیندازید.
6. ورودی‌های پیش‌فرض BarsToExport=300 و RefreshSeconds=5 را حفظ کنید. مجوز DLL یا معامله واقعی لازم نیست.
7. در سربرگ Experts یا Journal باید پیام **Bridge output** و سپس **Bridge: published** را ببینید.

EA یک فایل مانند metatr_EURUSD_M15.csv در FILE_COMMON می‌سازد. فقط با بسته‌شدن کندل جدید و رسیدن اولین تیکِ کندل بعدی آن را به‌روز می‌کند. اگر کمتر از ۵۲ کندل بسته‌شده قابل دریافت باشد، فایل تولید نمی‌شود.

## ۲. پیدا کردن فایل CSV در macOS

از Terminal مک این دستور را اجرا کنید:

    find "$HOME/Library/Application Support" -type f -name 'metatr_*.csv' -print 2>/dev/null

مسیر نمونه (فرضی؛ باید خروجی واقعی را از دستور بالا بردارید):

    /Users/USER/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files/metatr_EURUSD_M15.csv

اگر فایل پیدا نشد، پیام **Bridge output** را در Journal بررسی کنید. TerminalInfoString(TERMINAL_COMMONDATA_PATH) مسیر Windows/Wine را چاپ می‌کند و ممکن است لازم باشد مسیر متناظر آن در پوشه Wine macOS پیدا شود.

## ۳. اجرای Python در VS Code روی مک

داخل پوشه پروژه، پس از ساخت محیط مجازی نسخه 3.11 و نصب وابستگی‌ها:

    git pull
    source .venv/bin/activate
    python -m pip install -e ".[dev]"

**مسیر واقعی فایل CSV خودتان** را جایگزین /PATH/TO کنید:

    python -m metatr.bridge --file "/PATH/TO/metatr_EURUSD_M15.csv" --symbol EURUSD --timeframe M15

برای پیگیری سیگنال پس از هر کندل بسته‌شده:

    python -m metatr.bridge --file "/PATH/TO/metatr_EURUSD_M15.csv" --symbol EURUSD --timeframe M15 --watch --interval 2

Ctrl+C برای توقف. در حالت watch، خروجی JSONL روی stdout فقط برای **کندل جدید** صادر می‌شود. اگر فایل وجود نداشته باشد، هشدار انتظار در stderr می‌آید. این یک منبع داده واقعی *منتسب به فایل MT5* است، اما صحت قیمت بروکر را مستقل اثبات نمی‌کند.

## ۴. ساختار CSV و امنیت

نمونهٔ قراردادی CSV (اعداد صرفاً نمونه‌اند):

    timestamp,open,high,low,close,volume,symbol,timeframe,schema_version
    1760000000,1.10100,1.10200,1.10000,1.10150,312,EURUSD,M15,1

- timestamp: مقدار ثانیه‌ای MqlRates.time برای **زمان بازشدن کندل بسته‌شده**؛ Python آن را UTC epoch می‌خواند. اختلاف زمانی سرور بروکر با UTC را جداگانه بررسی کنید.
- volume: حجم تیک (tick_volume) است؛ **حجم واقعی معامله‌شده در فارکس نیست**.
- symbol/timeframe/schema_version در Python اعتبارسنجی می‌شوند.
- MQL5 ابتدا فایل موقت را کامل می‌نویسد و می‌بندد، سپس با FileMove فایل خروجی را جایگزین می‌کند.
- هیچ DLL، WebRequest، رمز، کلید API یا تابع OrderSend استفاده نشده است.

## ۵. عیب‌یابی

- **EA ظاهر نمی‌شود:** حتماً فایل را در MQL5/Experts مربوط به همان **Data Folder** گذاشته و Compile کنید.
- **CSV وجود ندارد:** Experts/Journal، تاریخچه نمودار، اتصال بروکر و مجوز دسترسی فایل را بررسی کنید.
- **خطای symbol یا timeframe:** آرگومان‌های CLI را دقیقاً با نماد و تایم‌فریم نمودار یکسان قرار دهید.
- **کمتر از ۵۲ کندل:** تاریخچهٔ کافی را در ترمینال بارگیری کنید.
- **فایل به‌روز نمی‌شود:** در بازار بسته، به‌طور طبیعی کندل جدید ایجاد نمی‌شود؛ تا دریافت تیک جدید نیز بسته‌شدن کندل شناسایی نمی‌شود.
- **خطای publish:** خطا در Experts/Journal ثبت می‌شود؛ احتمال مشکل دسترسی فایل در Wine را بررسی کنید.

## اسناد رسمی
- [FileOpen / FILE_COMMON](https://www.mql5.com/en/docs/files/fileopen)
- [CopyRates](https://www.mql5.com/en/docs/series/copyrates)
- [FileMove](https://www.mql5.com/en/docs/files/filemove)
- [EventSetTimer](https://www.mql5.com/en/docs/eventfunctions/eventsettimer)
- [MetaTrader 5 on macOS](https://www.mql5.com/en/articles/619)
