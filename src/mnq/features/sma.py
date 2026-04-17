"""[REAL] Simple moving average (arithmetic mean of last `length` values)."""
from __future__ import annotations

from collections import deque
from datetime import datetime

from mnq.core.types import Bar
from mnq.features._source import price_from_source


class SMA:
    __slots__ = ("length", "source", "_buf", "_value", "_last_update_bar_ts")

    def __init__(self, length: int, source: str = "close") -> None:
        if length < 2:
            raise ValueError("SMA length must be >= 2")
        self.length = int(length)
        self.source = source
        self._buf: deque[float] = deque(maxlen=self.length)
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    def update(self, bar: Bar) -> float | None:
        self._buf.append(price_from_source(bar, self.source))
        self._last_update_bar_ts = bar.ts
        if len(self._buf) >= self.length:
            self._value = sum(self._buf) / self.length
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
