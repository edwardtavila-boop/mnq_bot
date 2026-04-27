"""[REAL] Relative volume = volume / SMA(volume, length).

Pine v6 equivalent: `volume / ta.sma(volume, length)`, guarded against
divide-by-zero.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from mnq.core.types import Bar


class RelativeVolume:
    __slots__ = ("length", "_buf", "_sum", "_value", "_last_update_bar_ts")

    def __init__(self, length: int = 20) -> None:
        if length < 2:
            raise ValueError("RelativeVolume length must be >= 2")
        self.length = int(length)
        self._buf: deque[float] = deque(maxlen=self.length)
        self._sum: float = 0.0
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    def update(self, bar: Bar) -> float | None:
        v = float(bar.volume)
        self._last_update_bar_ts = bar.ts
        if len(self._buf) == self.length:
            self._sum -= self._buf[0]
        self._buf.append(v)
        self._sum += v
        if len(self._buf) >= self.length:
            avg = self._sum / self.length
            self._value = (v / avg) if avg > 0 else 0.0
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
