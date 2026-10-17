# Running MetaTrader 5 Python on Windows x64

1. Install MetaTrader 5 desktop terminal and Python 3.11 x64.
2. Start terminal and log in to a **demo** broker account.
3. In PowerShell:
```powershell
git clone https://github.com/abolfazl2600/metatr.git
cd metatr
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[mt5,dev]"
python -m pytest -q
python -m metatr.cli --source mt5 --symbol EURUSD --timeframe M15 --count 300
```
4. If a symbol is missing, inspect the exact broker symbol (e.g. EURUSD.a) in Market Watch and use its exact name.
5. Terminal must have access to enough chart history; `max bars`, market sessions and broker data quality can affect returned candles.

The MT5 adapter:
- calls `mt5.initialize()` with the terminal's active user session;
- reads only `copy_rates_from_pos(..., 1, count)`, omitting the forming bar;
- calls `mt5.shutdown()` even after an error;
- **cannot submit orders**.

Running Python via macOS ARM is deliberately unsupported. On a Mac, use offline sources or remote Windows development. Use Windows credentials and broker credentials only on trusted machines; do not commit them.
