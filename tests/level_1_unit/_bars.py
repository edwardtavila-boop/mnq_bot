"""Helpers for constructing synthetic Bars in tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mnq.core.types import Bar


def make_bar(
    ts: datetime, o: float, h: float, lo: float, c: float, v: int = 100, tf_sec: int = 60
) -> Bar:
    return Bar(
        ts=ts.astimezone(UTC),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=int(v),
        timeframe_sec=tf_sec,
    )


def constant_bars(
    n: int, price: float = 100.0, volume: int = 100, start: datetime | None = None, tf_sec: int = 60
) -> list[Bar]:
    start = start or datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    out: list[Bar] = []
    for i in range(n):
        t = start + timedelta(seconds=tf_sec * i)
        out.append(make_bar(t, price, price, price, price, volume, tf_sec))
    return out


def linear_close_bars(
    n: int,
    start_price: float = 100.0,
    slope: float = 1.0,
    volume: int = 100,
    start: datetime | None = None,
    tf_sec: int = 60,
) -> list[Bar]:
    start = start or datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    out: list[Bar] = []
    for i in range(n):
        t = start + timedelta(seconds=tf_sec * i)
        p = start_price + slope * i
        lo = p - 0.25
        hi = p + 0.25
        # Ensure open within [low, high]
        out.append(make_bar(t, p, hi, lo, p, volume, tf_sec))
    return out
