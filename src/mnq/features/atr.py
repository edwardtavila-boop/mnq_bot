"""[REAL] Average True Range (Pine v6 `ta.atr`).

Pine semantics:
    tr = max(high-low, |high - close[1]|, |low - close[1]|)  (first bar: tr = high-low)
    atr = ta.rma(tr, length)
"""
from __future__ import annotations

from datetime import datetime

from mnq.core.types import Bar
from mnq.features.rma import RMA


class ATR:
    __slots__ = ("length", "_rma", "_prev_close", "_value", "_last_update_bar_ts")

    def __init__(self, length: int = 14) -> None:
        if length < 2:
            raise ValueError("ATR length must be >= 2")
        self.length = int(length)
        self._rma = RMA(length=self.length, source="close")  # source unused; we pass TR directly
        self._prev_close: float | None = None
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    def update(self, bar: Bar) -> float | None:
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        self._last_update_bar_ts = bar.ts
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close

        # Feed TR into the RMA by simulating a bar where source == tr. RMA only
        # looks at `price_from_source(bar, source)`, so we pass a synthetic bar
        # whose `close` equals TR.
        self._value = _rma_step(self._rma, tr)
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


def _rma_step(rma: RMA, x: float) -> float | None:
    """Advance an RMA by a scalar (no bar needed)."""
    rma._count += 1
    if rma._value is None:
        rma._seed_buf.append(x)
        if rma._count >= rma.length:
            rma._value = sum(rma._seed_buf) / rma.length
        return rma._value
    rma._value = rma._alpha * x + (1.0 - rma._alpha) * rma._value
    return rma._value
