# گزارش پیشرفت و راهنمای اجرای پروژه روی مک (۱۷ اکتبر ۲۰۲۶)

این سند مراحل انجام‌شده در مخزن **abolfazl2600/metatr** و آزمایش‌های واقعی MetaTrader 5 روی macOS/Wine را ثبت می‌کند. مسیرهای محلی زیر مربوط به همان مک آزمایش‌شده هستند و لزوماً روی مک دیگر یکسان نیستند. دستورات از **Terminal مک** و از ریشه مخزن اجرا می‌شوند، مگر خلافش ذکر شده باشد.

**اصل ایمنی:** کل زنجیره پژوهشی، ساخت سیگنال، نمایش در MT5 و Paper Trading در این مرحله آزمایشی است؛ هیچ سفارش واقعی ارسال نمی‌شود. داده‌های ساخته‌شده با سوییچ --fixture مصنوعی‌اند، نه قیمت زنده Coinbase.

## ۱. چه کارهایی تا این لحظه انجام شده است؟

| مرحله | تغییرات ثبت‌شده | وضعیت در GitHub |
| --- | --- | --- |
| زیرساخت | Python package، VS Code، تست‌ها، GitHub Actions، خروجی sample/CSV، اعتبارسنجی کندل‌های بسته‌شده و EMA مرجع | موجود در main |
| پل قدیمی MT5 → Python | Expert Advisor خروجی CSV و خواننده Python؛ مستقل از نمایشگر جدید Coinbase | موجود در main؛ راهنمای docs/MAC_BRIDGE.md |
| داده Coinbase Futures | تشخیص قراردادهای BTC/BCH، جلوگیری از جایگزینی با Spot، ذخیره snapshot و کندل در SQLite | PRهای #8، #9، #10 ادغام شدند |
| رجیستری مدل | رجیستری SQLite مجزا، شناسه دقیق قرارداد/تایم‌فریم، نسخه‌های immutable و CLI، با execution=DISABLED | Issue #11 با PR #26 بسته شد |
| پژوهش و استراتژی | رابط استراتژی، EMA/Momentum، اجرای آزمایش قابل بازتولید و manifest پژوهش | PRهای #27 و #28 ادغام شدند |
| کیفیت و بک‌تست | کنترل کیفیت تاریخچه و تقویم، مشخصات قرارداد/هزینه، بک‌تست، walk-forward و گزارش حسابرسی | PRهای #29 تا #33 ادغام شدند |
| Python → MT5 | انتشار اتمیک snapshotهای CSV، سیگنال‌ها و manifest یک‌طرفه در FILE_COMMON | PR #34 ادغام شد |
| نمایشگر بومی MT5 | کد MetatrFuturesViewer.mq5، کندل/فلش/برچسب و وضعیت VALID/INVALID؛ اصلاح چارت تکراری در نسخه 1.01 | **PR #35 هنوز Draft و ادغام‌نشده است** |
| Paper Trading | حساب/دفترکل مجزا، کنترل ریسک و kill switch، شبیه‌ساز fill، runner و بازیابی | PRهای #36 تا #39 ادغام شدند؛ پذیرش عملیاتی Issue #25 باز است |
| تأیید end-to-end | مقایسه دقیق داده/سیگنال، آزمایش خرابی‌ها و تأیید نسخه جدید نمایشگر روی Wine | Issue #21 و پذیرش نهایی Issue #20 باز هستند |

این جدول وضعیت **پیاده‌سازی و شواهد موجود** است، نه ادعای سودآوری یا تکمیل تست عملیاتی طولانی‌مدت.

## ۲. اسم فولدرهای پروژه و وظیفه هرکدام

ساختار فایل‌های واقعی مخزن در شاخه main:

~~~text
metatr/                                 ← ریشه پروژه Git
├── .github/workflows/                  ← CI و تست smoke مربوط به Coinbase
├── .vscode/                            ← تنظیمات و launch مربوط به VS Code
├── config/markets.json                 ← تنظیمات بازار/پروفایل‌های پژوهش
├── docs/                                ← مستندات معماری و ماژول‌ها
├── examples/                            ← مثال‌ها
├── mql5/Experts/MetatrCSVBridge.mq5     ← پل قدیمی خروجی MT5 به Python
├── src/metatr/                          ← کدهای پایتون
│   ├── exchanges/                      ← Coinbase futures adapter
│   ├── paper/                          ← دفترکل، ریسک، شبیه‌سازی و runner کاغذی
│   ├── model_registry.py               ← رجیستری مدل و نسخه
│   ├── registry_cli.py                 ← دستورات رجیستری
│   ├── futures_cli.py                  ← بررسی بازار futures
│   ├── mt5_publish_cli.py              ← انتشار به MetaTrader
│   ├── mt5_publisher.py                ← فایل‌های خروجی و manifest
│   └── display_fixture.py              ← ساخت ۸۰ کندل مصنوعی برای تست
├── tests/                               ← تست‌های خودکار
├── pyproject.toml                       ← تنظیمات نصب و وابستگی‌ها
└── README.md                            ← راهنمای اصلی
~~~

موارد محلی زیر ممکن است بعد از نصب/اجرا ساخته شوند و **نباید به عنوان فولدرهای موجود در Git تصور شوند**:

~~~text
metatr/.venv/                            ← محیط مجازی Python (محلی)
metatr/data/raw/futures.sqlite          ← داده‌های خام Futures (در صورت ذخیره)
metatr/data/research/registry.sqlite    ← رجیستری مدل (در صورت ساخت)
metatr/data/paper/paper.sqlite          ← حساب Paper Trading (در صورت ساخت)
~~~

فایل نمایشگر جدید **هنوز فقط در شاخه PR #35** موجود است:

~~~text
mql5/Experts/MetatrFuturesViewer.mq5
docs/MT5_VIEWER.md
~~~

برای دیدن مسیر واقعی فولدر فعلی خود در Terminal:

~~~bash
pwd
git status --short
find . -maxdepth 2 -type d -not -path './.git*' -not -path './.venv*' | sort
~~~

## ۳. آماده‌سازی پروژه در Terminal مک

اگر پروژه را **از قبل دارید**، آن را دوباره clone نکنید؛ Terminal را داخل فولدر فعلی metatr باز کنید و ابتدا وضعیت فایل‌های محلی را بررسی کنید:

~~~bash
pwd
git status --short
git switch main
git pull --ff-only origin main
~~~

اگر پروژه روی دستگاه دیگری موجود نیست، **فقط در آن دستگاه** از ابتدا clone کنید:

~~~bash
git clone https://github.com/abolfazl2600/metatr.git
cd metatr
~~~

محیط و وابستگی‌ها:

~~~bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check .
~~~

اگر .venv قبلاً ساخته شده، تنها فعال‌سازی و نصب/آپدیت وابستگی‌ها کافی است:

~~~bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
~~~

نمونه تست‌های مرتبط:

~~~bash
python -m pytest -q tests/test_model_registry.py tests/test_mt5_publisher.py tests/test_paper_runner.py
python -m metatr.cli --source sample --symbol EURUSD --timeframe M15
~~~

خروجی sample و fixture **شبیه‌سازی** است. برای شناسایی Python فعال: which python و python --version را اجرا کنید.

## ۴. مسیر دقیق MetaTrader 5 روی همین مک

در آزمایش انجام‌شده، مسیر واقعی ریشه مشترک فایل‌ها این بوده است:

~~~text
/Users/abolfazl/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files
~~~

این فولدر همان **FILE_COMMON** است؛ با فولدر اختصاصی ترمینال یعنی MQL5/Files فرق دارد. در Terminal با متغیر زیر می‌توان فاصله‌های نام Application Support را بدون خطا مدیریت کرد:

~~~bash
MT5_COMMON_FILES="$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
ls -ld "$MT5_COMMON_FILES"
~~~

اگر نسخه Wine یا نام کاربر عوض شد، محل واقعی را پیدا کنید:

~~~bash
find "$HOME/Library/Application Support" -type d -path '*/MetaQuotes/Terminal/Common/Files' -print 2>/dev/null
~~~

برای قرار دادن Expert Advisor در متاتریدر: **MT5 → File → Open Data Folder → MQL5 → Experts**. این فولدر Experts محل سورس/فایل کامپایل‌شده است؛ **Common/Files** محل CSVهایی است که Python تولید می‌کند. این دو محل را با یکدیگر اشتباه نگیرید.

## ۵. دستور واقعی که برای ساخت داده آزمایشی اجرا کردیم

در فولدر metatr، با محیط مجازی فعال:

~~~bash
python3 -m metatr.mt5_publish_cli --fixture \
  --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --output-dir "/Users/abolfazl/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
~~~

فرم معادل، بدون دوباره تایپ‌کردن مسیر بلند:

~~~bash
MT5_COMMON_FILES="$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
python3 -m metatr.mt5_publish_cli --fixture \
  --market coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 \
  --output-dir "$MT5_COMMON_FILES"
~~~

این دستور بدون اتصال شبکه **۸۰ کندل بسته‌شده مصنوعی** و سیگنال‌های مدل momentum نسخه 1.0.0 را منتشر می‌کند. مسیر بازار در این آزمایش به این صورت ساخته شد:

~~~text
Common/Files/
└── metatr/
    └── 28c5db082e2e787e/
        ├── manifest.csv
        ├── bars_<run_id>.csv
        └── signals_<run_id>.csv
~~~

نام دو فایل وابسته به run_id است و ممکن است هنگام انتشار جدید تغییر کند؛ manifest.csv نام ثابت دارد و به فایل‌های درست اشاره می‌کند. مسیر زیر را با Terminal بررسی کنید:

~~~bash
MARKET_DIR="$MT5_COMMON_FILES/metatr/28c5db082e2e787e"
ls -lah "$MARKET_DIR"
head -n 2 "$MARKET_DIR/manifest.csv"
~~~

برای رندر معتبر، داده نباید قدیمی (STALE) شده باشد. اگر بعد از مدتی وضعیت STALE نمایش داده شد، دوباره fixture تازه منتشر کنید. انتشار مجدد در همان بازه کندل ممکن است همان run_id را نگه دارد؛ لزوماً به معنای ساخت نسل تازه نیست.

## ۶. نمایش داده در MT5: مراحل انجام‌شده

نمایشگر مستقل از پل قدیمی MetatrCSVBridge است؛ از Python داده را **به MT5 می‌فرستد** و هیچ دستوری برای معامله ارسال نمی‌کند.

چون فایل نمایشگر هنوز در main نیست، برای دریافت **نسخه فعلی PR #35** بدون تعویض شاخه کاری می‌توان از ریشه پروژه استفاده کرد:

~~~bash
git fetch origin feat/20-mt5-viewer
git show FETCH_HEAD:mql5/Experts/MetatrFuturesViewer.mq5 > /tmp/MetatrFuturesViewer.mq5
~~~

فایل /tmp/MetatrFuturesViewer.mq5 را در فولدر **MQL5/Experts** متعلق به ترمینال کپی کنید، سپس در MetaEditor باز کنید و با **F7 / Compile** کامپایل کنید. اگر قبلاً نسخه‌ای در آن فولدر بوده، قبل از جایگزینی از آن نسخه پشتیبان بگیرید. نصب واقعی این نسخه باید دوباره تأیید شود.

ورودی‌های آزمایش‌شده/موردانتظار Expert Advisor:

~~~text
CommonSubfolder = metatr\28c5db082e2e787e
ExpectedProduct = BIP-20DEC30-CDE
ExpectedStrategy = momentum
ExpectedVersion = 1.0.0
PollSeconds = 5
MaximumBars = 1000
MaximumAgeBars = 3
~~~

سپس از **Navigator → Expert Advisors** نمایشگر را به یک چارت میزبان اضافه کنید. چارت میزبان را باز نگه دارید؛ نمایشگر باید چارت سفارشی M15 با نامی مشابه این بسازد/پیدا کند:

~~~text
EXT_BIP_28c5db08,M15
~~~

زمان‌ها UTC هستند، نه الزاماً زمان بروکر. نمایشگر داده را به شکل M15 نشان می‌دهد؛ نه فید واقعی M1. در تصویر برچسب‌های EXTERNAL / SIMULATED DATA و execution=DISABLED باید باقی بمانند.

## ۷. نتیجه تست‌هایی که روی مک ثبت شد

**تأییدشده برای نسخه اولیه نمایشگر (1.00):**

- MetaEditor: اسکرین‌شات کامپایل بدون خطا و هشدار (0 errors، 0 warnings؛ X64 Regular).
- MT5/Wine: بازشدن نمودار سفارشی EXT_BIP_28c5db08,M15، نمایش کندل‌ها، فلش‌ها و NO_TRADE.
- Experts: ثبت EXTERNAL_VALID برای coinbase:BTC:FUTURE:BIP-20DEC30-CDE:M15 با ۸۰ کندل و ۸۰ سیگنال؛ execution=DISABLED.
- انتشار مجدد با همان نسل: نمودار پیش از راه‌اندازی مجدد بدون تب اضافه دیده شد.
- بستن و بازکردن MT5: نمودار مجدداً باز شد، اما در اسکرین‌شات بعدی **دو تب مشابه** دیده شد.
- آزمایش حذف موقت manifest.csv: داده‌های قبلی باقی ماند و وضعیت STALE/INVALID ظاهر شد؛ با برگرداندن manifest، وضعیت به VALID بازگشت.

**اصلاح آماده‌شده، اما هنوز تأیید بومی نشده:** در commit f7b50b4 از PR #35، نسخه 1.01 نمایشگر برای پیدا کردن و استفاده مجدد از چارت موجود به‌جای ساخت چارت تکراری تغییر کرده است. نسخه 1.01 باید دوباره در MetaEditor کامپایل و پس از restart تست شود؛ کامپایل قبلی نسخه 1.00، نسخه جدید را تأیید نمی‌کند.

## ۸. تکرار تست سالم/خراب/بازیابی روی داده آزمایشی

ابتدا با دستور بخش ۵ یک fixture تازه بسازید و منتظر نمایش VALID در MT5 باشید. سپس **فقط روی خروجی fixture آزمایشی** و زمانی که هیچ ناشری در حال نوشتن فایل نیست:

~~~bash
MARKET_DIR="$MT5_COMMON_FILES/metatr/28c5db082e2e787e"
mv "$MARKET_DIR/manifest.csv" "$MARKET_DIR/manifest.csv.testbak"
~~~

بعد از چند ثانیه وضعیت باید INVALID یا STALE شود و **آخرین کندل‌های معتبر پاک نشوند**. برای بازیابی سریع:

~~~bash
mv "$MARKET_DIR/manifest.csv.testbak" "$MARKET_DIR/manifest.csv"
~~~

اکنون باید دوباره VALID نمایش داده شود؛ اگر منقضی شده بود، fixture جدید بسازید. این آزمون صرفاً مسیر خطای فایل گم‌شده را پوشش می‌دهد. فایل‌های واقعی بازار را عمداً حذف یا ویرایش نکنید.

برای تست چارت تکراری، بعد از کامپایل نسخه **1.01**، تب‌های تکراری از اجرای نسخه قدیمی را دستی ببندید، یک چارت مناسب نگه دارید، MT5 را ببندید و باز کنید، و تعداد تب‌ها و پیام Experts را ثبت کنید. بدون مشاهده نتیجه، موفقیت نسخه جدید را اعلام نکنید.

## ۹. مرحله‌های باقی‌مانده (هنوز Done نیستند)

1. کامپایل نسخه 1.01 از PR #35 در MetaEditor با ثبت build شماره MT5، تعداد خطا/هشدار و زمان تست.
2. بررسی بازشدن مجدد ترمینال، استفاده مجدد از چارت موجود، بستن دستی چارت و بازسازی درست بدون تب‌های اضافی.
3. تولید نسل داده جدید در بازه زمانی مناسب؛ تطبیق دقیق UTC/OHLCV، شناسه مدل، action و run_id بین CSV و نمودار.
4. تست فایل ناقص، checksum نامعتبر، schema/product اشتباه، timestamp قدیمی و انتشار قطع‌شده با حفظ تصویر معتبر قبلی و بازیابی.
5. ثبت شواهد پذیرش در PR #35 و docs/MT5_VIEWER.md، سپس review/merge فقط بعد از تکمیل معیارهای Issue #20.
6. اجرای پذیرش سرتاسری Issue #21؛ مستقل از آن، پذیرش عملیاتی و مشاهده طولانی‌مدت Paper Trading مربوط به Issue #25 نیز هنوز باز است.

**نکته:** تکمیل تست‌های Python یا سبزشدن CI، جای آزمایش native در MetaEditor و MT5/Wine را نمی‌گیرد.

## ۱۰. دستورات کاربردی Git برای ادامه کار

~~~bash
cd /path/to/your/metatr
git status --short
git branch --show-current
git fetch origin
git switch main
git pull --ff-only origin main
source .venv/bin/activate
python -m pytest -q
ruff check .
~~~

اگر تغییرات محلی ذخیره‌نشده دارید، پیش از git switch یا git pull آن‌ها را commit/stash کنید؛ دستورات بالا را کورکورانه روی تغییرات خود اجرا نکنید. پروژه روی branch main توسعه می‌یابد و قابلیت‌های مستقل پس از PR و review به آن اضافه می‌شوند.

## ۱۱. لینک‌های مرجع در مخزن

- راهنمای نصب روی مک: [SETUP_MAC.md](SETUP_MAC.md)
- پل قدیمی دریافت CSV از متاتریدر: [MAC_BRIDGE.md](MAC_BRIDGE.md)
- فرمت انتشار Python → MT5: [MT5_OUTBOUND.md](MT5_OUTBOUND.md)
- رجیستری مدل: [MODEL_REGISTRY.md](MODEL_REGISTRY.md)
- پژوهش و بک‌تست: [RESEARCH.md](RESEARCH.md) و [BACKTEST.md](BACKTEST.md)
- عملیات Paper Trading: [PAPER_OPERATIONS.md](PAPER_OPERATIONS.md)
- نمایشگر نسخه در حال بررسی: [PR #35 و docs/MT5_VIEWER.md در شاخه PR](https://github.com/abolfazl2600/metatr/pull/35)
- [Issue #20](https://github.com/abolfazl2600/metatr/issues/20)، [Issue #21](https://github.com/abolfazl2600/metatr/issues/21)، [Issue #25](https://github.com/abolfazl2600/metatr/issues/25)

آخرین بروزرسانی این گزارش: **۲۰۲۶-۱۰-۱۷**.