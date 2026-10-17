# Closed-candle CSV format

This file is **an example of the schema**, not a real market dataset.

```csv
timestamp,open,high,low,close,volume
2026-01-01T00:00:00Z,1.10,1.11,1.09,1.105,100
2026-01-01T00:15:00Z,1.105,1.12,1.10,1.115,120
```

A real CSV needs at least `slow_period + 2` completed candles (default 52). Source must exclude the current incomplete candle. In financial experiments, do not look ahead and do not use hindsight-revised records as if they were known in the past.
