"""[REAL] Timezone and session-time helpers. CME-aware.

Conventions:
- All internal times are timezone-aware UTC.
- Display/session times use America/New_York (CME's display timezone).
- Trading day boundaries follow CME, not calendar: a "trading day" is
  18:00 prior calendar day (CT) → 17:00 calendar day (CT) for futures.

For MNQ scalping during RTH only, we use the simpler 09:30-16:00 ET
windows defined in specs. ETH support is here but unused by v0.x specs.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
UTC = UTC


def to_ny(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime")
    return ts.astimezone(NY)


def to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime")
    return ts.astimezone(UTC)


def parse_hhmm(s: str) -> time:
    """'09:30' -> time(9, 30)."""
    h, m = s.split(":")
    return time(int(h), int(m))


def in_window_ny(ts: datetime, start: str, end: str) -> bool:
    """Is `ts` within [start, end) NY local time on its NY-local date?"""
    ny = to_ny(ts)
    s, e = parse_hhmm(start), parse_hhmm(end)
    return s <= ny.time() < e


def session_start_ny(ts: datetime, hhmm: str = "09:30") -> datetime:
    """Return the NY-local session start datetime (UTC) for the date of `ts`."""
    ny = to_ny(ts)
    s = parse_hhmm(hhmm)
    start_local = ny.replace(hour=s.hour, minute=s.minute, second=0, microsecond=0)
    return start_local.astimezone(UTC)


def session_end_ny(ts: datetime, hhmm: str = "16:00") -> datetime:
    ny = to_ny(ts)
    e = parse_hhmm(hhmm)
    end_local = ny.replace(hour=e.hour, minute=e.minute, second=0, microsecond=0)
    return end_local.astimezone(UTC)


def seconds_since_session_open(ts: datetime, open_hhmm: str = "09:30") -> int:
    delta = ts - session_start_ny(ts, open_hhmm)
    return int(delta.total_seconds())


def is_rth(ts: datetime) -> bool:
    """Regular Trading Hours: 09:30-16:00 ET, Mon-Fri (no holiday check here)."""
    ny = to_ny(ts)
    if ny.weekday() >= 5:
        return False
    return in_window_ny(ts, "09:30", "16:00")


def floor_to_minute(ts: datetime, m: int = 1) -> datetime:
    """Round ts down to the start of the m-minute bucket."""
    discard = timedelta(
        minutes=ts.minute % m,
        seconds=ts.second,
        microseconds=ts.microsecond,
    )
    return ts - discard
