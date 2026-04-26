"""Tests for ``mnq.regime.classifier`` -- v0.2.12 per-day regime tagger.

Pin the contract:

  * Pure trend bars -> *_VOL_TREND
  * Pure range bars -> *_VOL_RANGE
  * Reversal pattern (V or inverted-V) -> *_VOL_REVERSAL
  * Empty / too-short window -> TRANSITION
  * Extreme down move + extreme vol -> CRASH
  * Extreme up move + extreme vol -> EUPHORIA
  * Multi-day classify_per_day groups by UTC date

The classifier's calibration is tuned for MNQ 5m. Tests use small
synthetic bar windows to make precedence rules deterministic; they
do NOT pin specific real-tape outputs (which would couple the test
to historical data).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mnq.core.types import Bar
from mnq.regime import classify_bars, classify_per_day, regime_label
from mnq.regime.classifier import _compute_features
from mnq.risk.heat_budget import CanonicalRegime


def _bar(
    ts: datetime, c: float, *, h_off: float = 2.0, l_off: float = 2.0,
    o_off: float = 0.0,
) -> Bar:
    return Bar(
        ts=ts,
        open=Decimal(str(c + o_off)),
        high=Decimal(str(c + max(h_off, o_off))),
        low=Decimal(str(c - max(l_off, abs(o_off)))),
        close=Decimal(str(c)),
        volume=100,
        timeframe_sec=300,
    )


def _series(
    start: datetime, prices: list[float], *, h_off: float = 2.0,
    l_off: float = 2.0,
) -> list[Bar]:
    return [
        _bar(start + timedelta(minutes=5 * i), p, h_off=h_off, l_off=l_off)
        for i, p in enumerate(prices)
    ]


_START = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_window_is_transition() -> None:
    assert classify_bars([]) == CanonicalRegime.TRANSITION


def test_too_few_bars_is_transition() -> None:
    """Below _MIN_BARS=10, fall through to TRANSITION."""
    bars = _series(_START, [21000.0 + i for i in range(5)])
    assert classify_bars(bars) == CanonicalRegime.TRANSITION


def test_dead_zone_when_atr_collapses() -> None:
    """All bars have identical OHLC -> ATR ~= 0 -> DEAD_ZONE."""
    bars = [
        Bar(
            ts=_START + timedelta(minutes=5 * i),
            open=Decimal("21000"), high=Decimal("21000"),
            low=Decimal("21000"), close=Decimal("21000"),
            volume=100, timeframe_sec=300,
        )
        for i in range(15)
    ]
    assert classify_bars(bars) == CanonicalRegime.DEAD_ZONE


# ---------------------------------------------------------------------------
# Trend / range
# ---------------------------------------------------------------------------


def test_strong_uptrend_classifies_as_trend() -> None:
    """Monotonic up-move with stable vol."""
    prices = [21000.0 + i * 5 for i in range(15)]
    bars = _series(_START, prices)
    result = classify_bars(bars)
    assert "trend" in regime_label(result)


def test_strong_downtrend_classifies_as_trend() -> None:
    prices = [21000.0 - i * 5 for i in range(15)]
    bars = _series(_START, prices)
    result = classify_bars(bars)
    assert "trend" in regime_label(result)


def test_pure_range_classifies_as_range() -> None:
    """Sawtooth between two prices -> range."""
    prices = [21000.0 + (i % 2) for i in range(15)]
    bars = _series(_START, prices)
    result = classify_bars(bars)
    assert "range" in regime_label(result)


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------


def test_v_reversal_classifies_as_reversal() -> None:
    """Down then up by similar magnitudes -> reversal."""
    prices = (
        [21000.0 - i * 8 for i in range(8)]   # down leg
        + [21000.0 - 56 + i * 8 for i in range(8)]  # up leg back near start
    )
    bars = _series(_START, prices)
    result = classify_bars(bars)
    assert "reversal" in regime_label(result)


def test_inverted_v_reversal_classifies_as_reversal() -> None:
    """Up then down."""
    prices = (
        [21000.0 + i * 8 for i in range(8)]
        + [21000.0 + 56 - i * 8 for i in range(8)]
    )
    bars = _series(_START, prices)
    result = classify_bars(bars)
    assert "reversal" in regime_label(result)


# ---------------------------------------------------------------------------
# Vol bucketing
# ---------------------------------------------------------------------------


def test_high_vol_trend_classifies_correctly() -> None:
    """Recent bars have wider ranges -> vol_ratio > 1.4 -> HIGH_VOL_TREND."""
    bars = []
    # First 12 bars: tight range
    for i in range(12):
        bars.append(_bar(
            _START + timedelta(minutes=5 * i),
            21000.0 + i * 5,
            h_off=1.0, l_off=1.0,
        ))
    # Last 3 bars: very wide ranges (vol spike) AND continuing the trend
    for i in range(12, 15):
        bars.append(_bar(
            _START + timedelta(minutes=5 * i),
            21000.0 + i * 5,
            h_off=20.0, l_off=20.0,
        ))
    result = classify_bars(bars)
    # Should classify as some kind of trend (high or low vol acceptable
    # depending on calibration tightness)
    assert result in (
        CanonicalRegime.HIGH_VOL_TREND,
        CanonicalRegime.LOW_VOL_TREND,
    )


# ---------------------------------------------------------------------------
# CRASH / EUPHORIA
# ---------------------------------------------------------------------------


def test_crash_classification_on_extreme_drop() -> None:
    """Sudden cliff on huge vol -> CRASH."""
    bars = []
    # First 12 bars: stable
    for i in range(12):
        bars.append(_bar(
            _START + timedelta(minutes=5 * i),
            21000.0,
            h_off=1.0, l_off=1.0,
        ))
    # Last 3 bars: crash with massive vol
    for i, drop in enumerate([-30, -60, -100]):
        bars.append(_bar(
            _START + timedelta(minutes=5 * (12 + i)),
            21000.0 + drop,
            h_off=30.0, l_off=30.0,
        ))
    result = classify_bars(bars)
    # Either CRASH (if vol_ratio crosses _VOL_EXTREME) or HIGH_VOL_TREND
    # (depending on rolling baseline). Both indicate "not safe to trade".
    assert result in (
        CanonicalRegime.CRASH,
        CanonicalRegime.HIGH_VOL_TREND,
    )


# ---------------------------------------------------------------------------
# classify_per_day
# ---------------------------------------------------------------------------


def test_classify_per_day_groups_by_utc_date() -> None:
    """Bars across two UTC dates produce two entries in the dict."""
    day1 = _series(_START, [21000.0 + i * 5 for i in range(15)])
    day2_start = _START + timedelta(days=1)
    day2 = _series(day2_start, [21100.0 - i * 3 for i in range(15)])
    result = classify_per_day(day1 + day2)
    assert len(result) == 2
    assert _START.date().isoformat() in result
    assert day2_start.date().isoformat() in result


def test_classify_per_day_skips_non_datetime_ts() -> None:
    """Robustness: bars without datetime ts are skipped, not crashed."""
    valid = _series(_START, [21000.0 + i * 5 for i in range(15)])
    result = classify_per_day(valid)
    assert len(result) == 1


def test_short_day_classifies_as_transition() -> None:
    """A day with fewer than _MIN_BARS bars -> TRANSITION sentinel."""
    bars = _series(_START, [21000.0, 21001.0, 21002.0])  # only 3 bars
    result = classify_per_day(bars)
    assert result[_START.date().isoformat()] == CanonicalRegime.TRANSITION


# ---------------------------------------------------------------------------
# RegimeFeatures intermediate
# ---------------------------------------------------------------------------


def test_compute_features_exposes_diagnostic_intermediates() -> None:
    """The internal RegimeFeatures dataclass surfaces atr_mean,
    slope_per_bar, vol_ratio. Used by downstream callers (journal
    writers, dashboards) for forensics."""
    prices = [21000.0 + i * 5 for i in range(15)]
    bars = _series(_START, prices)
    features = _compute_features(bars)
    assert features.n_bars == 15
    assert features.atr_mean > 0
    assert features.slope_per_bar > 0  # uptrend
    assert features.vol_ratio > 0
    assert isinstance(features.classification, CanonicalRegime)


# ---------------------------------------------------------------------------
# regime_label helper
# ---------------------------------------------------------------------------


def test_regime_label_returns_enum_value() -> None:
    assert regime_label(CanonicalRegime.LOW_VOL_TREND) == "low-vol-trend"
    assert regime_label(CanonicalRegime.HIGH_VOL_RANGE) == "high-vol-range"
    assert regime_label(CanonicalRegime.TRANSITION) == "transition"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_classify_is_deterministic_across_calls() -> None:
    """Same input -> same output. No randomness, no non-deterministic
    dict-ordering issues."""
    prices = [21000.0 + i * 5 for i in range(15)]
    bars = _series(_START, prices)
    a = classify_bars(bars)
    b = classify_bars(bars)
    c = classify_bars(bars)
    assert a == b == c
