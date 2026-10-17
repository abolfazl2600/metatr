# اجرای پروژه روی مک‌بوک M2

## پیش‌نیازها
- macOS + VS Code + افزونه Python
- Git و Python 3.11 یا 3.12
- اینترنت برای دریافت کتابخانه‌ها و کلون‌کردن مخزن

## مراحل
```bash
git clone https://github.com/abolfazl2600/metatr.git
cd metatr
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
python -m metatr.cli --source sample --count 300
```

در VS Code با File > Open Folder پوشهٔ پروژه را باز کنید. از Command Palette گزینهٔ Python: Select Interpreter و سپس `.venv/bin/python` را انتخاب کنید. تنظیم `.vscode/settings.json` برای ساختار macOS است؛ در Windows interpreter را به `.venv\\Scripts\\python.exe` تغییر دهید.

## نکتهٔ MT5
نسخه macOS ترمینال (معمولاً از طریق Wine) **اتصال رسمی Python API بومی به آن نیست**. برای دادهٔ واقعی MT5، دستور CLI را در ویندوز x64 دارای ترمینال فعال اجرا کنید؛ یا در V1 داده‌های *کندل بسته‌شده* را از آن سیستم به CSV انتقال دهید.

## بررسی نتیجه
خروجی دستور sample دارای `synthetic: true` است. خروجی BUY/SELL/NO_TRADE در sample صرفاً برای آزمایش منطق و روند توسعه تولید شده است.
