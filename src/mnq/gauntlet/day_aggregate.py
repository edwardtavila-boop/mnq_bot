"""Per-day gauntlet aggregation for Apex V3 integration.

Batch 5D/6B. Runs the 12-gate gauntlet at representative bar(s) within
a day and produces a single aggregate score that can be blended into
the Apex V3 delta for the gate contract.

The representative bar is the bar at the day's highest volume moment
(most likely trade entry time). This is a reasonable proxy since the
gauntlet evaluates whether conditions at that moment support trading.

Usage:

    from mnq.gauntlet.day_aggregate import gauntlet_day_score

    score = gauntlet_day_score(bars, regime="trend_up")
    # score.delta in [-1.0, +1.0]
    # score.pass_rate in [0.0, 1.0]
    # score.voice in [-100, +100]
"""

from __future__ import annotations

from dataclasses import dataclass

from mnq.core.types import Bar
from mnq.gauntlet.bridge import context_from_bars
from mnq.gauntlet.gates.gauntlet12 import run_gauntlet
from mnq.gauntlet.scorer_bridge import (
    failed_gate_names,
    gate_pass_rate,
    gauntlet_delta,
    gauntlet_voice,
)


@dataclass(frozen=True, slots=True)
class GauntletDayScore:
    """Aggregate gauntlet score for one day."""

    delta: float  # [-1.0, +1.0]
    voice: float  # [-100, +100]
    pass_rate: float  # [0.0, 1.0]
    n_passed: int
    n_failed: int
    failed_gates: list[str]
    eval_bar_idx: int  # which bar was evaluated


def _peak_volume_bar_idx(bars: list[Bar]) -> int:
    """Find the bar index with highest volume (representative trade moment)."""
    if not bars:
        return 0
    best_idx = 0
    best_vol = 0
    for i, b in enumerate(bars):
        if b.volume > best_vol:
            best_vol = b.volume
            best_idx = i
    return best_idx


def gauntlet_day_score(
    bars: list[Bar],
    *,
    regime: str | None = None,
    side: str = "long",
    loss_streak: int = 0,
    intermarket_corr: float | None = None,
    spread_ticks: float | None = None,
    high_impact_events_minutes: list[int] | None = None,
) -> GauntletDayScore:
    """Evaluate the gauntlet at the day's peak-volume bar.

    Returns a GauntletDayScore with delta, voice, pass_rate, and
    failed gate details — ready for blending into the Apex delta.
    """
    if not bars:
        return GauntletDayScore(
            delta=0.0,
            voice=0.0,
            pass_rate=0.0,
            n_passed=0,
            n_failed=0,
            failed_gates=[],
            eval_bar_idx=0,
        )

    bar_idx = _peak_volume_bar_idx(bars)
    ctx = context_from_bars(
        bars,
        bar_idx,
        side=side,
        regime=regime,
        loss_streak=loss_streak,
        intermarket_corr=intermarket_corr,
        spread_ticks=spread_ticks,
        high_impact_events_minutes=high_impact_events_minutes,
    )
    verdicts = run_gauntlet(ctx)
    n_passed = sum(1 for v in verdicts if v.pass_)
    n_failed = len(verdicts) - n_passed

    return GauntletDayScore(
        delta=gauntlet_delta(verdicts),
        voice=gauntlet_voice(verdicts, weighted=True),
        pass_rate=gate_pass_rate(verdicts),
        n_passed=n_passed,
        n_failed=n_failed,
        failed_gates=failed_gate_names(verdicts),
        eval_bar_idx=bar_idx,
    )


def blend_deltas(
    apex_delta: float,
    gauntlet_delta: float,
    *,
    gauntlet_weight: float = 0.15,
) -> float:
    """Blend Apex V3 delta with gauntlet delta.

    The gauntlet_delta is in [-1.0, +1.0] while apex_delta is typically
    in [-0.15, +0.15]. We scale the gauntlet delta to the same range
    before blending.

    Default weight: 15% gauntlet, 85% Apex. This is conservative —
    the gauntlet should nudge but not override the 15-voice engine.

    Returns:
        Blended delta in approximately [-0.20, +0.20].
    """
    # Scale gauntlet to apex-delta magnitude (~0.15 range)
    gauntlet_scaled = gauntlet_delta * 0.15
    return (1 - gauntlet_weight) * apex_delta + gauntlet_weight * gauntlet_scaled
