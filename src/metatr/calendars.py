"""Explicit, sourced contract-specific closure windows; no inferred weekends."""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

from metatr.futures_core import Market
from metatr.futures_store import TIMEFRAME_SECONDS


def stamp(value) -> pd.Timestamp:
    value = pd.Timestamp(value)
    if pd.isna(value) or value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("Explicit UTC required")
    return value


@dataclass(frozen=True)
class SessionCalendar:
    market: Market
    calendar_id: str
    version: str
    source: str
    timezone: str
    effective_start: str
    effective_end: str
    # Explicit [start,end) no-trading windows, pinned to source/version.
    closures: tuple[tuple[str, str], ...] = ()

    def expected(self, market: Market, start, end) -> pd.DatetimeIndex:
        start, end = stamp(start), stamp(end)
        if self.market != market or not all((self.calendar_id, self.version, self.source)):
            raise ValueError("Unverified or mismatched session calendar")
        ZoneInfo(self.timezone)
        if start < stamp(self.effective_start) or end > stamp(self.effective_end):
            raise ValueError("Calendar outside its sourced coverage")
        step = TIMEFRAME_SECONDS[market.timeframe]
        expected = pd.date_range(start, end, freq=pd.Timedelta(seconds=step), inclusive="left")
        previous = stamp(self.effective_start)
        for left, right in self.closures:
            left, right = stamp(left), stamp(right)
            if (left < previous or left >= right or left < stamp(self.effective_start)
                    or right > stamp(self.effective_end)
                    or left.value % (step * 10**9) or right.value % (step * 10**9)):
                raise ValueError("Invalid/overlapping calendar closure windows")
            previous = right
            expected = expected[(expected < left) | (expected >= right)]
        return expected
