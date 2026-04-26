"""[REAL] Bar-level regime classifier.

Maps a window of :class:`mnq.core.types.Bar` objects to one of the
10 canonical regimes defined in :class:`mnq.risk.heat_budget.CanonicalRegime`.
The output is deterministic for a given input.

Why this exists
---------------
v0.2.7's spec_payload approximates ``regimes_approved`` as "any
positive-PnL day -> normal_vol_trend"; that's a stub. The Firm
MacroAgent reads this field to decide whether the strategy's
historical wins came in conditions that *could happen again*. A
strategy that won every day in low-vol-range conditions but is
about to be deployed in a high-vol-trend environment is the canonical
regime-mismatch failure mode.

This classifier replaces the stub: per-day classification of the
cached backtest, then ``regimes_approved`` becomes the SET of regimes
where the variant has positive expectancy. New variants without
backtest history fall back to the empty set (the Firm correctly
flags this as "no regime evidence").

Method
------
The classifier reduces a bar window to two axes:

  * **Volatility regime** (LOW / NORMAL / HIGH / EXTREME) computed
    from the ATR of the window normalized by the rolling ATR mean.
  * **Direction regime** (TREND / RANGE / REVERSAL) computed from a
    linear-regression slope of close prices, normalized by ATR, with
    a side check on the start-vs-end direction.

The two axes combine to form 6 of the 10 canonical regimes; the
remaining 4 (CRASH, EUPHORIA, DEAD_ZONE, TRANSITION) are special
cases caught by guards before the main classification. See
``classify_bars`` for the precedence order.

Calibration constants are tuned conservatively for MNQ 5m bars and
are documented inline. They CAN be wrong; if a downstream consumer
sees a clearly mis-classified day the right move is to widen the
test fixture, not to silently shift the threshold.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from mnq.core.types import Bar
from mnq.risk.heat_budget import CanonicalRegime

# ---------------------------------------------------------------------------
# Calibration constants
# ---------------------------------------------------------------------------

# Minimum bar count for a stable classification. Below this, fall
# through to TRANSITION.
_MIN_BARS = 10

# Vol bucket thresholds, in units of bar-window ATR / rolling ATR mean.
# Tuned for MNQ 5m: a one-sigma move in vol is roughly +/-30% of mean.
_VOL_LOW = 0.7
_VOL_HIGH = 1.4
_VOL_EXTREME = 2.5  # CRASH / EUPHORIA territory

# Trend slope normalization. ``slope_per_bar / atr`` is the canonical
# units; >0.15 of an ATR per bar is meaningful trend, <0.05 is range.
_SLOPE_TREND = 0.15
_SLOPE_RANGE = 0.05

# Reversal: closing-price flip across the window (sign change of total
# return) AND nontrivial directional movement on each leg.
_REVERSAL_LEG_FRACTION = 0.4  # leg must be >= 40% of total range


@dataclass(frozen=True)
class RegimeFeatures:
    """Diagnostic intermediates exposed for the runtime / journal."""

    n_bars: int
    atr_mean: float
    atr_window: float
    vol_ratio: float            # atr_window / atr_mean
    slope_per_bar: float        # OLS slope of close vs bar index
    slope_norm: float           # slope_per_bar / atr_mean
    total_return: float         # last_close - first_close (signed)
    leg1_extreme: float         # min/max close in first half (vs first)
    leg2_extreme: float         # min/max close in second half (vs last)
    classification: CanonicalRegime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _true_range(bar: Bar, prev_close: Decimal | None) -> float:
    """Wilder True Range for a bar."""
    hl = float(bar.high - bar.low)
    if prev_close is None:
        return hl
    hc = abs(float(bar.high - prev_close))
    lc = abs(float(bar.low - prev_close))
    return max(hl, hc, lc)


def _atr(bars: Sequence[Bar]) -> float:
    """Simple ATR = mean True Range across the window."""
    if not bars:
        return 0.0
    prev = None
    total = 0.0
    for b in bars:
        total += _true_range(b, prev)
        prev = b.close
    return total / len(bars)


def _ols_slope(closes: Sequence[float]) -> float:
    """Ordinary least-squares slope of `y = a + b*x` for x = 0..n-1.

    Uses closed-form for speed and to avoid numpy dependency in this
    light module.
    """
    n = len(closes)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(closes) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(closes):
        dx = i - x_mean
        num += dx * (y - y_mean)
        den += dx * dx
    return num / den if den > 0 else 0.0


def _compute_features(bars: Sequence[Bar]) -> RegimeFeatures:
    """Reduce a bar window to the diagnostic features used by classify_bars."""
    n = len(bars)
    closes = [float(b.close) for b in bars]
    atr_window = _atr(bars)

    # Rolling ATR mean: take the prior 80% of the window as "rolling
    # baseline" so the very recent bars don't dominate.
    baseline_n = max(1, int(n * 0.8))
    atr_mean = _atr(bars[:baseline_n]) if baseline_n > 0 else atr_window
    if atr_mean <= 0:
        atr_mean = atr_window if atr_window > 0 else 1e-9

    slope_per_bar = _ols_slope(closes)
    slope_norm = slope_per_bar / atr_mean
    total_return = closes[-1] - closes[0] if n >= 2 else 0.0

    # Reversal detection: largest path-traveled vs net return.
    # If the closes swept a large range but ended near the start, that's
    # a reversal regardless of direction. Use the "internal extremum"
    # principle: the max or min should occur somewhere in the middle,
    # not at the endpoints.
    if n >= 4:
        range_traveled = max(closes) - min(closes)
        max_idx = closes.index(max(closes))
        min_idx = closes.index(min(closes))
        # Internal index = not at the very first or last bar
        max_internal = 1 <= max_idx <= n - 2
        min_internal = 1 <= min_idx <= n - 2
        leg1_extreme = range_traveled if (max_internal or min_internal) else 0.0
        leg2_extreme = range_traveled if (max_internal or min_internal) else 0.0
    else:
        range_traveled = 0.0
        leg1_extreme = leg2_extreme = 0.0

    classification = _classify(
        n=n,
        vol_ratio=atr_window / atr_mean,
        atr_mean=atr_mean,
        slope_norm=slope_norm,
        total_return=total_return,
        leg1=leg1_extreme,
        leg2=leg2_extreme,
        atr_window=atr_window,
    )

    return RegimeFeatures(
        n_bars=n,
        atr_mean=atr_mean,
        atr_window=atr_window,
        vol_ratio=atr_window / atr_mean,
        slope_per_bar=slope_per_bar,
        slope_norm=slope_norm,
        total_return=total_return,
        leg1_extreme=leg1_extreme,
        leg2_extreme=leg2_extreme,
        classification=classification,
    )


def _classify(
    *, n: int,
    vol_ratio: float,
    atr_mean: float,
    slope_norm: float,
    total_return: float,
    leg1: float,
    leg2: float,
    atr_window: float,
) -> CanonicalRegime:
    """Map features -> CanonicalRegime. Precedence:

      1. Too few bars -> TRANSITION
      2. Extreme vol + strong direction -> CRASH or EUPHORIA
      3. Extremely low vol -> DEAD_ZONE
      4. Reversal pattern (large leg1 + large leg2 in opposite signs)
         -> {LOW,HIGH}_VOL_REVERSAL
      5. Trend (|slope_norm| >= _SLOPE_TREND) -> {LOW,HIGH}_VOL_TREND
      6. Range (|slope_norm| < _SLOPE_RANGE) -> {LOW,HIGH}_VOL_RANGE
      7. Otherwise -> TRANSITION
    """
    if n < _MIN_BARS:
        return CanonicalRegime.TRANSITION

    # Edge: nearly-zero ATR -> DEAD_ZONE
    if atr_mean < 1e-6:
        return CanonicalRegime.DEAD_ZONE

    # Extreme moves with extreme vol -> CRASH / EUPHORIA
    if vol_ratio >= _VOL_EXTREME:
        if slope_norm < -_SLOPE_TREND:
            return CanonicalRegime.CRASH
        if slope_norm > _SLOPE_TREND:
            return CanonicalRegime.EUPHORIA

    # Volatility bucket
    if vol_ratio < _VOL_LOW:
        # Very tight ranges might be DEAD_ZONE if atr_window is also low
        if atr_window < atr_mean * 0.4:
            return CanonicalRegime.DEAD_ZONE
        is_high_vol = False
    elif vol_ratio > _VOL_HIGH:
        is_high_vol = True
    else:
        is_high_vol = False  # NORMAL bucket maps to LOW for simplicity

    # Reversal: large path range traveled but small net return.
    # leg1_extreme / leg2_extreme are 0 when the extremes occurred at
    # the endpoints (i.e. monotonic moves -- those are TREND not
    # REVERSAL). When non-zero, they equal the range_traveled.
    leg1_abs = abs(leg1)
    is_reversal = (
        leg1_abs > 0  # extreme is internal (not endpoint)
        and leg1_abs >= 4 * atr_window
        and abs(total_return) < leg1_abs * 0.5
    )
    if is_reversal:
        return (
            CanonicalRegime.HIGH_VOL_REVERSAL if is_high_vol
            else CanonicalRegime.LOW_VOL_REVERSAL
        )

    # Trend / Range based on normalized slope
    if abs(slope_norm) >= _SLOPE_TREND:
        return (
            CanonicalRegime.HIGH_VOL_TREND if is_high_vol
            else CanonicalRegime.LOW_VOL_TREND
        )
    if abs(slope_norm) < _SLOPE_RANGE:
        return (
            CanonicalRegime.HIGH_VOL_RANGE if is_high_vol
            else CanonicalRegime.LOW_VOL_RANGE
        )

    # Mid-zone: in between trend and range -> TRANSITION
    return CanonicalRegime.TRANSITION


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_bars(bars: Sequence[Bar]) -> CanonicalRegime:
    """Classify a single window of bars to a CanonicalRegime.

    Returns ``CanonicalRegime.TRANSITION`` when the window is too short
    to produce a stable classification (n < _MIN_BARS=10).
    """
    return _compute_features(bars).classification


def classify_per_day(bars: Sequence[Bar]) -> dict[str, CanonicalRegime]:
    """Group ``bars`` by UTC date and classify each day's window.

    Returns ``{ "YYYY-MM-DD": CanonicalRegime }``. Days with too few
    bars get TRANSITION. Skipping the dict for missing dates would be
    silently lossy, so days that fail the _MIN_BARS check are still
    represented (with the TRANSITION sentinel).
    """
    by_day: dict[str, list[Bar]] = defaultdict(list)
    for b in bars:
        ts = b.ts
        if not isinstance(ts, datetime):
            continue
        day = ts.astimezone(UTC).date().isoformat()
        by_day[day].append(b)
    out: dict[str, CanonicalRegime] = {}
    for day, day_bars in sorted(by_day.items()):
        out[day] = classify_bars(day_bars)
    return out


def regime_label(regime: CanonicalRegime) -> str:
    """Human-readable string for a regime (matches the enum value).

    Helper used by the spec_payload renderer so we don't expose the
    raw enum to the Firm payload (which expects strings).
    """
    return regime.value
