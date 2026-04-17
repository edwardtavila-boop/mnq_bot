"""[REAL] Higher-timeframe feature wrapper.

Aggregates primary-timeframe bars into HTF bars and feeds them to an
inner feature instance. Lookahead-safe: only reports the inner
feature's *previous-complete-HTF* value during an open HTF bar. This
matches Pine v6 `request.security(... , lookahead = barmerge.lookahead_off)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from mnq.core.types import Bar


class _InnerFeature(Protocol):
    def update(self, bar: Bar) -> float | None: ...
    @property
    def value(self) -> float | None: ...
    @property
    def ready(self) -> bool: ...


_HTF_SEC = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


@dataclass
class _AggregatingBar:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0

    def to_bar(self, timeframe_sec: int) -> Bar:
        return Bar(
            ts=self.ts,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            timeframe_sec=timeframe_sec,
        )


class HTFWrapper:
    """Wrap an inner feature to run on an HTF stream aggregated from primary bars.

    Reports `self.value` as the inner feature's value *as of the last completed
    HTF bar*. The current in-progress HTF bar is accumulated silently — no
    lookahead.
    """

    __slots__ = ("_inner", "_htf_sec", "_cur", "_cur_bucket", "_last_completed_value", "_last_update_bar_ts")

    def __init__(self, inner: _InnerFeature, timeframe: str) -> None:
        if timeframe not in _HTF_SEC:
            raise ValueError(f"unsupported HTF timeframe: {timeframe!r}")
        self._inner = inner
        self._htf_sec = _HTF_SEC[timeframe]
        self._cur: _AggregatingBar | None = None
        self._cur_bucket: int | None = None
        self._last_completed_value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    def _bucket(self, ts: datetime) -> int:
        # Buckets anchored at UTC epoch. Accepts tz-aware datetimes (required by Bar).
        ts_utc = ts.astimezone(UTC)
        return int(ts_utc.timestamp()) // self._htf_sec

    def update(self, bar: Bar) -> float | None:
        self._last_update_bar_ts = bar.ts
        bucket = self._bucket(bar.ts)
        if self._cur_bucket is None:
            # First bar. Start the first HTF bucket.
            self._cur_bucket = bucket
            self._cur = _AggregatingBar(
                ts=bar.ts, open=bar.open, high=bar.high, low=bar.low,
                close=bar.close, volume=bar.volume,
            )
            return self._last_completed_value

        if bucket != self._cur_bucket:
            # The previous HTF bar has just completed. Feed it into the inner
            # feature and record the resulting value as the "as-of-last-close"
            # reading.
            assert self._cur is not None
            completed = self._cur.to_bar(self._htf_sec)
            self._inner.update(completed)
            self._last_completed_value = self._inner.value

            # Start a new HTF bucket with the current primary bar.
            self._cur_bucket = bucket
            self._cur = _AggregatingBar(
                ts=bar.ts, open=bar.open, high=bar.high, low=bar.low,
                close=bar.close, volume=bar.volume,
            )
            return self._last_completed_value

        # Same bucket: accumulate.
        assert self._cur is not None
        if bar.high > self._cur.high:
            self._cur.high = bar.high
        if bar.low < self._cur.low:
            self._cur.low = bar.low
        self._cur.close = bar.close
        self._cur.volume += bar.volume
        return self._last_completed_value

    @property
    def value(self) -> float | None:
        return self._last_completed_value

    @property
    def ready(self) -> bool:
        return self._last_completed_value is not None

    @property
    def last_update_bar_ts(self) -> datetime | None:
        """Timestamp of the last bar ingested."""
        return self._last_update_bar_ts
