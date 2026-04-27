"""[REAL] Session-anchored VWAP.

Resets at session boundary. The session boundary is detected via a
provided `session_day_key` callable: whenever the key changes relative
to the previous bar, the cumulative sums reset.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from mnq.core.types import Bar

# Type alias for compatibility
datetime_or_none = datetime | None


def _default_day_key(ts: datetime) -> str:
    return ts.date().isoformat()


class VWAP:
    """Session-anchored volume-weighted average price.

    `anchor` is a semantic string ("session", "week", "month") — only
    "session" resets daily are implemented; the other anchors fall back
    to session and log a warning via `anchor` being stored for the caller
    to inspect. The generated strategy passes anchor="session" only.
    """

    __slots__ = (
        "anchor",
        "_day_key_fn",
        "_cum_pv",
        "_cum_v",
        "_cur_key",
        "_value",
        "_last_update_bar_ts",
    )

    def __init__(
        self,
        anchor: str = "session",
        day_key_fn: Callable[[datetime], str] | None = None,
    ) -> None:
        self.anchor = anchor
        self._day_key_fn = day_key_fn or _default_day_key
        self._cum_pv: float = 0.0
        self._cum_v: float = 0.0
        self._cur_key: str | None = None
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    def update(self, bar: Bar) -> float | None:
        # typical price = (high + low + close) / 3
        tp = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
        v = float(bar.volume)
        key = self._day_key_fn(bar.ts)
        self._last_update_bar_ts = bar.ts
        if key != self._cur_key:
            self._cum_pv = 0.0
            self._cum_v = 0.0
            self._cur_key = key
        self._cum_pv += tp * v
        self._cum_v += v
        self._value = (self._cum_pv / self._cum_v) if self._cum_v > 0 else tp
        return self._value

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def last_update_bar_ts(self) -> datetime | None:
        """Timestamp of the last bar ingested."""
        return self._last_update_bar_ts
