"""[REAL] Price-source lookup shared by feature classes."""
from __future__ import annotations

from mnq.core.types import Bar


def price_from_source(bar: Bar, source: str) -> float:
    """Resolve the Pine-compatible price source name to a scalar from the bar."""
    if source == "open":
        return float(bar.open)
    if source == "high":
        return float(bar.high)
    if source == "low":
        return float(bar.low)
    if source == "close":
        return float(bar.close)
    if source == "volume":
        return float(bar.volume)
    if source == "hl2":
        return (float(bar.high) + float(bar.low)) / 2.0
    if source == "hlc3":
        return (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
    if source == "ohlc4":
        return (float(bar.open) + float(bar.high) + float(bar.low) + float(bar.close)) / 4.0
    raise ValueError(f"unknown price source: {source!r}")
