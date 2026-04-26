"""Regime classification.

Maps a window of OHLCV bars to one of the 10 canonical regimes
defined in :mod:`mnq.risk.heat_budget` (CanonicalRegime). Used by
``mnq.spec.runtime_payload`` (v0.2.12) to populate the
``regimes_approved`` field of the spec_payload with real per-day
classifications instead of the v0.2.7 stub ("any positive-PnL day
counts as normal_vol_trend").
"""
from __future__ import annotations

from mnq.regime.classifier import (
    classify_bars,
    classify_per_day,
    regime_label,
)

__all__ = [
    "classify_bars",
    "classify_per_day",
    "regime_label",
]
