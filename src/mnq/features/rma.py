"""[REAL] Wilder's smoothing (RMA, Pine v6 `ta.rma`).

Pine semantics:
    alpha = 1 / length
    rma[0] = sma(source, length) at bar length-1
    rma[i] = alpha * source[i] + (1 - alpha) * rma[i-1]
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from mnq.core.types import Bar
from mnq.features._source import price_from_source


class RMA:
    __slots__ = ("length", "source", "_alpha", "_seed_buf", "_value", "_count", "_last_update_bar_ts")

    def __init__(self, length: int, source: str = "close") -> None:
        if length < 2:
            raise ValueError("RMA length must be >= 2")
        self.length = int(length)
        self.source = source
        self._alpha = 1.0 / self.length
        self._seed_buf: deque[float] = deque(maxlen=self.length)
        self._value: float | None = None
        self._count = 0
        self._last_update_bar_ts: datetime | None = None

    def update(self, bar: Bar) -> float | None:
        x = price_from_source(bar, self.source)
        self._count += 1
        self._last_update_bar_ts = bar.ts
        if self._value is None:
            self._seed_buf.append(x)
            if self._count >= self.length:
                self._value = sum(self._seed_buf) / self.length
            return self._value
        self._value = self._alpha * x + (1.0 - self._alpha) * self._value
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
