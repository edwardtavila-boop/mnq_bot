"""Bridge between bar data and the 12-gate gauntlet.

Constructs a :class:`GauntletContext` from a list of bars at a given
trade entry point, enabling the gauntlet to evaluate any signal against
the 12 gates without the caller needing to know the internal field
layout.

Batch 5A. This is the first step toward wiring the gauntlet as a live
pre-filter in the execution path.

Usage:

    from mnq.gauntlet.bridge import context_from_bars
    from mnq.gauntlet.gates.gauntlet12 import run_gauntlet, verdict_summary

    ctx = context_from_bars(bars, bar_idx=42, side="long", regime="trend_up")
    verdicts = run_gauntlet(ctx)
    summary = verdict_summary(verdicts)
    if summary["allow"]:
        # forward the trade
"""

from __future__ import annotations

from mnq.core.types import Bar, Side
from mnq.gauntlet.gates.gauntlet12 import GauntletContext
from mnq.gauntlet.orderflow import orderflow_from_bars

# ---------------------------------------------------------------------------
# Regime label mapping
# ---------------------------------------------------------------------------
# strategy_ab labels days with "real_*" prefixes; gauntlet gates expect
# the base vocabulary (trend_up, trend_down, chop, etc.).
# "real_high_vol" has no direct equivalent — we map it to "high_vol"
# which the regime gate doesn't recognize → treated as non-confirming
# (score 0.0, gate fails). This is intentional: high-vol days are
# inherently uncertain.
# "real_range" → "range" (also non-confirming — tight range days are
# low opportunity).

_REGIME_MAP: dict[str, str] = {
    "real_trend_up": "trend_up",
    "real_trend_down": "trend_down",
    "real_chop": "chop",
    "real_high_vol": "high_vol",
    "real_range": "range",
    # Firm regime classifier labels (already correct vocabulary)
    "trend_up": "trend_up",
    "trend_down": "trend_down",
    "chop": "chop",
}


def normalize_regime(raw: str | None) -> str | None:
    """Map any regime label to the gauntlet's expected vocabulary.

    Returns ``None`` for unknown labels — the regime gate will PASS
    with a "stub" note (graceful degradation).
    """
    if raw is None:
        return None
    return _REGIME_MAP.get(raw, raw)


def _ema(values: list[float], span: int) -> float | None:
    """Simple exponential moving average over the last ``span`` values."""
    if len(values) < span:
        return None
    k = 2.0 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def context_from_bars(
    bars: list[Bar],
    bar_idx: int,
    *,
    side: str | Side = "long",
    regime: str | None = None,
    loss_streak: int = 0,
    high_impact_events_minutes: list[int] | None = None,
    intermarket_corr: float | None = None,
    spread_ticks: float | None = None,
    es_closes: list[float] | None = None,
    lookback: int = 30,
    ema_fast_span: int = 9,
    ema_slow_span: int = 21,
) -> GauntletContext:
    """Build a GauntletContext from the bars window ending at ``bar_idx``.

    Extracts closes, highs, lows, volumes from
    ``bars[max(0, bar_idx - lookback) : bar_idx + 1]``, computes EMA(9)
    and EMA(21) for the fast/slow pair, and fills in all scalar fields.

    Fields that require external data (loss_streak, intermarket_corr,
    spread_ticks, high_impact_events_minutes) are passed through as-is.
    If not provided, the corresponding gates return PASS with a "stub"
    note — the gauntlet is designed for graceful degradation on missing
    data.

    Args:
        bars: Full day's bar list.
        bar_idx: 0-based index of the trade's entry bar.
        side: Trade direction ("long" / "short" or Side enum).
        regime: Optional regime classifier output. Accepts raw labels
            from any source (e.g. ``"real_trend_up"`` from strategy_ab);
            automatically normalized to gauntlet vocabulary.
        loss_streak: Consecutive losses preceding this trade.
        high_impact_events_minutes: Minutes-to-event offsets for nearby
            HIGH-impact news events. Empty = no events.
        intermarket_corr: ES/NQ correlation at this moment.
        spread_ticks: Synthetic bid-ask spread in ticks.
        lookback: Number of prior bars to include for indicators.
        ema_fast_span: Fast EMA period (default 9).
        ema_slow_span: Slow EMA period (default 21).

    Returns:
        Populated GauntletContext ready for ``run_gauntlet()``.
    """
    side_str = side.value if isinstance(side, Side) else str(side)

    # Window of bars up to and including bar_idx
    start = max(0, bar_idx - lookback)
    window = bars[start : bar_idx + 1]

    closes = [float(b.close) for b in window]
    highs = [float(b.high) for b in window]
    lows = [float(b.low) for b in window]
    volumes = [int(b.volume) for b in window]

    # EMA fast/slow + their prior-bar values
    ema_fast = _ema(closes, ema_fast_span)
    ema_slow = _ema(closes, ema_slow_span)

    # Prior-bar EMAs (shift window back by 1)
    if len(closes) >= 2:
        closes_prev = closes[:-1]
        ema_fast_prev = _ema(closes_prev, ema_fast_span)
        ema_slow_prev = _ema(closes_prev, ema_slow_span)
    else:
        ema_fast_prev = None
        ema_slow_prev = None

    # Entry bar's timestamp
    entry_bar = bars[bar_idx] if bar_idx < len(bars) else bars[-1]

    # Order flow features (Batch 7A) — compute from bars in window
    of_snap = orderflow_from_bars(window, eval_bar_idx=len(window) - 1)

    return GauntletContext(
        now=entry_bar.ts,
        bar_index=bar_idx,
        side=side_str,
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_fast_prev=ema_fast_prev,
        ema_slow_prev=ema_slow_prev,
        loss_streak=loss_streak,
        high_impact_events_minutes=high_impact_events_minutes or [],
        regime=normalize_regime(regime),
        intermarket_corr=intermarket_corr,
        spread_ticks=spread_ticks,
        cvd=of_snap.cvd,
        bar_delta=of_snap.bar_delta,
        imbalance=of_snap.imbalance,
        absorption_score=of_snap.absorption_score,
        buy_aggressor_pct=of_snap.buy_aggressor_pct,
        es_closes=es_closes or [],
    )
