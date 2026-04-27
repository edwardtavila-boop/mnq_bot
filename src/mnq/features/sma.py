"""[REAL] Simple moving average (arithmetic mean of last `length` values).

Implementation note (scorecard bundle v0.1 — Apr 2026):
    Uses O(1) running sum rather than re-summing the deque on every bar.
    The deque still bounds the window (maxlen = length). When a new value
    pushes the buffer past capacity the left-most element is evicted; we
    subtract it from `_sum` *before* the append so the running total stays
    aligned with `_buf`.

    Numerical note: a long-running float accumulator can drift by O(eps * n)
    after n updates. For MNQ 5-minute bars over a full session (~78 bars)
    or even a week (~400 bars) that drift is far below tick precision
    (0.25 / 20000 ≈ 1e-5). If we ever need sub-ULP correctness we can add
    a periodic resync via sum(self._buf).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from mnq.core.types import Bar
from mnq.features._source import price_from_source


class SMA:
    __slots__ = ("length", "source", "_buf", "_sum", "_value", "_last_update_bar_ts")

    def __init__(self, length: int, source: str = "close") -> None:
        if length < 2:
            raise ValueError("SMA length must be >= 2")
        self.length = int(length)
        self.source = source
        self._buf: deque[float] = deque(maxlen=self.length)
        self._sum: float = 0.0
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    def update(self, bar: Bar) -> float | None:
        x = price_from_source(bar, self.source)
        # Maintain the running sum in O(1). When the deque is already at
        # capacity, the next append will evict buf[0] — subtract it first.
        if len(self._buf) == self.length:
            self._sum -= self._buf[0]
        self._buf.append(x)
        self._sum += x
        self._last_update_bar_ts = bar.ts
        if len(self._buf) >= self.length:
            self._value = self._sum / self.length
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
