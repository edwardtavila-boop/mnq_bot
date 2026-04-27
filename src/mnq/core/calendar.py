"""[REAL] CME Globex equity-index futures (NQ/MNQ/ES/MES) session calendar.

Uses pandas_market_calendars under the hood for holiday data, but provides
a stable, typed interface for executor / blackout code without pandas types.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

import pandas_market_calendars as mcal
import pytz  # type: ignore[import-untyped]


@dataclass(frozen=True)
class SessionWindow:
    """A trading session window (RTH, ETH, pre_open, post_close)."""

    start: datetime  # UTC
    end: datetime  # UTC
    kind: str  # "RTH" | "ETH" | "pre_open" | "post_close"
    is_half_day: bool


# CME equity-index half-days. Christmas Eve, day after Thanksgiving, July 4, etc.
_HALF_DAYS: Final[frozenset[tuple[int, int]]] = frozenset(
    [
        (12, 23),  # Dec 23 (Christmas Eve if it's a weekday)
        (11, 27),  # day after Thanksgiving
        (7, 4),  # July 4
        (7, 5),  # day after July 4 if July 4 is Fri or weekday
    ]
)


class CMEFuturesCalendar:
    """CME equity-index futures (NQ/MNQ/ES/MES) session calendar.

    RTH (Regular Trading Hours):
        ET 9:30 - 16:00 (9:30 AM - 4:00 PM New York)
        On half-days: 9:30 - 13:00 (closes at 1 PM)

    ETH (Electronic Trading Hours):
        Globex overnight session: previous day 17:00 ET to next day 09:30 ET
        (4 PM close to 9:30 AM open)

    Holidays and half-days are sourced from pandas_market_calendars.
    """

    def __init__(self, name: str = "CME Globex Equity") -> None:
        """Initialize calendar, using the CME Globex Equity schedule."""
        try:
            # Use "CME Globex Equity" for CME Globex equities futures
            self._cal = mcal.get_calendar(name)
        except Exception as exc:
            raise ValueError(f"Failed to load calendar '{name}': {exc}") from exc
        # Cache valid days; will be populated on first use
        self._valid_days_cache: dict[tuple[date, date], set[date]] = {}

    def is_trading_day(self, d: date) -> bool:
        """Return True if the date is a trading day (not a weekend or holiday)."""
        # Use valid_days() from pandas_market_calendars
        # Get a year of data centered around the target date
        year_start = date(d.year, 1, 1)
        year_end = date(d.year, 12, 31)
        cache_key = (year_start, year_end)

        if cache_key not in self._valid_days_cache:
            valid_index = self._cal.valid_days(year_start, year_end)
            valid_set = {pd_ts.date() for pd_ts in valid_index}
            self._valid_days_cache[cache_key] = valid_set
        else:
            valid_set = self._valid_days_cache[cache_key]

        return d in valid_set

    def is_half_day(self, d: date) -> bool:
        """Return True if trading day is a half-day (early close at 1 PM ET)."""
        if not self.is_trading_day(d):
            return False
        # Check explicit half-day dates (month, day)
        return (d.month, d.day) in _HALF_DAYS

    def rth_window(self, d: date) -> SessionWindow | None:
        """Return the RTH (regular) session window for date d in UTC.

        Returns None if d is not a trading day.
        RTH is 9:30 - 16:00 ET (or 9:30 - 13:00 on half-days).
        """
        if not self.is_trading_day(d):
            return None

        et = pytz.timezone("America/New_York")
        open_et = et.localize(datetime(d.year, d.month, d.day, 9, 30, 0))
        start_utc = open_et.astimezone(UTC)

        if self.is_half_day(d):
            close_et = et.localize(datetime(d.year, d.month, d.day, 13, 0, 0))
        else:
            close_et = et.localize(datetime(d.year, d.month, d.day, 16, 0, 0))
        end_utc = close_et.astimezone(UTC)

        return SessionWindow(
            start=start_utc,
            end=end_utc,
            kind="RTH",
            is_half_day=self.is_half_day(d),
        )

    def eth_window(self, d: date) -> SessionWindow | None:
        """Return the ETH (Globex overnight) window for date d in UTC.

        ETH runs from 4 PM ET previous day to 9:30 AM ET current day.
        Returns None if the session day (d) is not a trading day.
        """
        if not self.is_trading_day(d):
            return None

        et = pytz.timezone("America/New_York")

        prev_day = d - timedelta(days=1)
        start_et = et.localize(datetime(prev_day.year, prev_day.month, prev_day.day, 17, 0, 0))
        start_utc = start_et.astimezone(UTC)

        end_et = et.localize(datetime(d.year, d.month, d.day, 9, 30, 0))
        end_utc = end_et.astimezone(UTC)

        return SessionWindow(
            start=start_utc,
            end=end_utc,
            kind="ETH",
            is_half_day=False,
        )

    def next_trading_day(self, d: date) -> date:
        """Return the next trading day after d."""
        candidate = d + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def prev_trading_day(self, d: date) -> date:
        """Return the previous trading day before d."""
        candidate = d - timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate

    def is_in_rth(self, ts: datetime) -> bool:
        """Return True if ts (UTC) falls within RTH of any trading day."""
        if ts.tzinfo is None:
            raise ValueError("is_in_rth requires timezone-aware datetime")
        # Normalize to UTC for consistency
        ts_utc = ts.astimezone(UTC).replace(tzinfo=UTC)

        # Check for each day around ts
        et = pytz.timezone("America/New_York")
        # Rough conversion: find what date this is in ET
        ts_et = ts_utc.astimezone(et)
        test_date = ts_et.date()

        for day_offset in [-1, 0, 1]:  # Check prev, current, next day
            test_day = test_date + timedelta(days=day_offset)
            window = self.rth_window(test_day)
            if window and window.start <= ts_utc <= window.end:
                return True
        return False

    def is_in_blackout(
        self,
        ts: datetime,
        *,
        pre_open_min: int = 2,
        post_close_min: int = 2,
    ) -> bool:
        """Return True if ts (UTC) is in a blackout window.

        Blackout windows are:
          - pre_open_min minutes before RTH open
          - post_close_min minutes after RTH close

        Returns False if ts is not near any trading day.
        """
        if ts.tzinfo is None:
            raise ValueError("is_in_blackout requires timezone-aware datetime")

        ts_utc = ts.astimezone(UTC).replace(tzinfo=UTC)

        et = pytz.timezone("America/New_York")
        ts_et = ts_utc.astimezone(et)
        test_date = ts_et.date()

        for day_offset in [-1, 0, 1]:
            test_day = test_date + timedelta(days=day_offset)
            window = self.rth_window(test_day)
            if window:
                pre_open = window.start - timedelta(minutes=pre_open_min)
                post_close = window.end + timedelta(minutes=post_close_min)

                if pre_open <= ts_utc <= window.start:
                    return True
                if window.end <= ts_utc <= post_close:
                    return True

        return False

    def quarterly_roll_date(self, contract: str, year: int) -> date:
        """Return the last-trade-date (LTD) for a quarterly contract.

        MNQ/NQ/ES/MES are quarterly (H/M/U/Z month codes).
        Rolls are the 2nd Thursday before the 3rd Friday of contract month.

        Args:
            contract: e.g., 'NQH26' or 'MNQZ25'
            year: 4-digit year (used to anchor the calculation)

        Returns:
            date of the last trade date (expiry).
        """
        # Parse month code from contract
        month_code = contract[-3]  # e.g., 'H' from 'NQH26'
        month_map = {"H": 3, "M": 6, "U": 9, "Z": 12}
        if month_code not in month_map:
            raise ValueError(f"Invalid month code '{month_code}' in contract '{contract}'")

        month = month_map[month_code]

        # Find the 3rd Friday of the contract month
        import calendar

        # Get all days in the month
        _, last_day = calendar.monthrange(year, month)
        third_friday = None
        fridays_found = 0
        for day in range(1, last_day + 1):
            d = date(year, month, day)
            if d.weekday() == 4:  # Friday = 4
                fridays_found += 1
                if fridays_found == 3:
                    third_friday = d
                    break

        if third_friday is None:
            raise ValueError(f"Could not find 3rd Friday for {month}/{year}")

        # Roll is 2nd Thursday before the 3rd Friday
        # Go backwards from 3rd Friday to find the 2nd preceding Thursday
        thursdays_found = 0
        current = third_friday - timedelta(days=1)
        while thursdays_found < 2:
            if current.weekday() == 3:  # Thursday = 3
                thursdays_found += 1
                if thursdays_found == 2:
                    return current
            current -= timedelta(days=1)

        raise ValueError(f"Could not find roll date for {contract} {year}")
