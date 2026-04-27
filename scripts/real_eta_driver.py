"""Real Apex V3 per-day driver for `firm_vs_baseline --with-apex-gate`.

Batch 3F: replace the deterministic-hash snapshot in
``_synthetic_day_apex_pm_output`` with real per-day runs of
``eta_v3_framework.firm_engine.evaluate()`` over actual bar sequences.

Pipeline
--------
Given a day's ``list[mnq.core.types.Bar]`` we:

1. Translate each mnq Bar → apex ``firm_engine.Bar`` (int unix secs, float OHLCV).
2. Stream the bars through ``IndicatorState`` so ATR/EMA/RSI/ADX/VWAP are
   populated exactly like the production Apex engine.
3. Run ``V1Detector.detect`` on each bar to emit setup triggers.
4. Call ``firm_engine.evaluate(...)`` per bar — one ``FirmDecision`` out.
5. Aggregate per-day: mean ``pm_final``, peak ``voice_agree``, dominant
   direction (mode of nonzero directions), any ``fire_long or fire_short``
   as ``engine_live``.
6. Emit a dict shaped identically to
   ``scripts.firm_vs_baseline._synthetic_day_apex_pm_output`` so
   ``mnq.eta_v3.gate.apex_gate`` accepts it unchanged.

The math for ``delta`` mirrors PM's fold-in (0.80 * base + 0.20 *
voice_agree/15 + corroboration/dissent bonus). That preserves the gate
contract (-0.10 skip / -0.05 reduced / +0.02 corroborate) so the only
thing that changes is the *signal source* — from hash → real engine.
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
APEX_PY = REPO_ROOT / "eta_v3_framework" / "python"
for p in (str(APEX_PY),):
    if p not in sys.path:
        sys.path.insert(0, p)

# These are the eta_v3_framework python modules; they import each other
# via flat names (``from firm_engine import Bar`` etc.), so the path
# insertion above is required.
from backtest import V1Detector, V1DetectorConfig  # noqa: E402
from firm_engine import Bar as ApexBar  # noqa: E402
from firm_engine import FirmConfig, FirmDecision, detect_regime, evaluate  # noqa: E402
from indicator_state import IndicatorState  # noqa: E402

# mnq.Bar is a frozen dataclass with Decimal OHLC + datetime ts + int volume.
from mnq.core.types import Bar as MnqBar  # noqa: E402

__all__ = [
    "run_day_through_apex",
    "day_pm_output_from_real_apex",
]


def _to_apex_bar(b: MnqBar) -> ApexBar:
    """Translate an mnq.Bar → eta_v3_framework.firm_engine.Bar.

    apex's Bar wants ``time`` as int unix seconds and OHLCV as floats.
    """
    return ApexBar(
        time=int(b.ts.timestamp()),
        open=float(b.open),
        high=float(b.high),
        low=float(b.low),
        close=float(b.close),
        volume=float(b.volume),
    )


def run_day_through_apex(
    bars: Iterable[MnqBar],
    *,
    recent_losses: int = 0,
    cfg: FirmConfig | None = None,
) -> list[FirmDecision]:
    """Stream a day of mnq.Bar through the real Apex V3 engine.

    Returns one ``FirmDecision`` per bar.
    """
    cfg = cfg or FirmConfig()
    state = IndicatorState()
    detector = V1Detector(cfg=V1DetectorConfig())
    decisions: list[FirmDecision] = []

    for i, mb in enumerate(bars):
        ab = _to_apex_bar(mb)
        state.update(ab)
        st = detector.detect(i, ab, state)
        atr_ma20 = state.atr_ma20()
        vol_z = state.vol_z()
        regime = detect_regime(
            ab.adx if ab.adx is not None else 20.0,
            ab.atr if ab.atr is not None else 0.0,
            atr_ma20,
            vol_z,
        )
        d = evaluate(
            bar=ab,
            st=st,
            regime=regime,
            atr_ma20=atr_ma20,
            vol_z=vol_z,
            prev_adx_3=state.adx_3_bars_ago(),
            range_avg_20=state.range_avg_20(),
            vol_z_prev_1=state.vol_z_at(1),
            vol_z_prev_2=state.vol_z_at(2),
            highest_5_prev=state.highest_5_prev(),
            lowest_5_prev=state.lowest_5_prev(),
            recent_losses=recent_losses,
            prev_day_high=state.prev_day_high,
            prev_day_low=state.prev_day_low,
            cfg=cfg,
        )
        decisions.append(d)
    return decisions


def _aggregate_day(decisions: list[FirmDecision]) -> dict[str, Any]:
    """Reduce a day's per-bar decisions to a single PM-shaped summary.

    * ``voice_agree`` — max over the day (the strongest moment of
      agreement is what the gate should see)
    * ``pm_final``    — mean over the day, rounded to 1dp
    * ``direction``   — mode of nonzero directions (1, -1, or 0 if none)
    * ``engine_live`` — any ``fire_long or fire_short`` during the day
    * ``fire_count``  — how many bars actually fired (diagnostic only)
    """
    if not decisions:
        return {
            "voice_agree": 0,
            "pm_final": 0.0,
            "direction": 0,
            "engine_live": False,
            "fire_count": 0,
            "setup_names": [],
        }

    voice_agree = max(d.voice_agree for d in decisions)
    pm_final = sum(d.pm_final for d in decisions) / len(decisions)
    fire_count = sum(1 for d in decisions if d.fire_long or d.fire_short)
    engine_live = fire_count > 0

    nz_dirs = [d.direction for d in decisions if d.direction != 0]
    direction = Counter(nz_dirs).most_common(1)[0][0] if nz_dirs else 0

    setup_names = sorted({d.setup_name for d in decisions if d.setup_name})

    return {
        "voice_agree": int(voice_agree),
        "pm_final": round(float(pm_final), 2),
        "direction": int(direction),
        "engine_live": bool(engine_live),
        "fire_count": int(fire_count),
        "setup_names": setup_names,
    }


def day_pm_output_from_real_apex(
    bars: Iterable[MnqBar],
    *,
    base_probability: float = 0.6,
    recent_losses: int = 0,
    cfg: FirmConfig | None = None,
    gauntlet_delta: float | None = None,
    gauntlet_weight: float = 0.15,
) -> dict[str, Any]:
    """One day's bars → PM-shaped dict that ``apex_gate`` will accept.

    This is the drop-in replacement for
    ``_synthetic_day_apex_pm_output`` — same output schema, real signal.

    When ``gauntlet_delta`` is provided (Batch 5D/6B), it is blended
    into the final delta at ``gauntlet_weight`` (default 15%). This
    allows the 12-gate gauntlet to nudge the Apex gate without
    overriding the 15-voice engine.
    """
    decisions = run_day_through_apex(bars, recent_losses=recent_losses, cfg=cfg)
    agg = _aggregate_day(decisions)

    voice_agree = agg["voice_agree"]
    pm_final = agg["pm_final"]
    direction = agg["direction"]
    engine_live = agg["engine_live"]

    # PM fold-in math (same as the synthetic version, so delta thresholds
    # in apex_gate stay comparable between snapshot and real runs):
    strong = voice_agree >= 12
    # Direction conflict is unknowable without a baseline bias — treat
    # "engine said fire but net direction is 0" OR "both sides fired
    # during the day" as conflict. Aggregation keeps only the dominant
    # direction, so we proxy conflict off a per-decision split.
    long_fires = sum(1 for d in decisions if d.fire_long)
    short_fires = sum(1 for d in decisions if d.fire_short)
    direction_conflict = long_fires > 0 and short_fires > 0

    blended = 0.80 * base_probability + 0.20 * (voice_agree / 15.0)
    bonus = 0.05 if (strong and engine_live) else 0.0
    penalty = 0.05 if (strong and direction_conflict) else 0.0
    adjusted = max(0.0, min(1.0, blended + bonus - penalty))
    apex_only_delta = adjusted - base_probability

    # V16 blend: fold gauntlet delta into the composite signal
    if gauntlet_delta is not None:
        # Scale gauntlet [-1, +1] to apex-delta magnitude (~0.15)
        gauntlet_scaled = gauntlet_delta * 0.15
        delta = (1 - gauntlet_weight) * apex_only_delta + gauntlet_weight * gauntlet_scaled
    else:
        delta = apex_only_delta

    return {
        "verdict": "GO",  # the gate still applies; PM verdict upstream is a separate concern
        "probability": adjusted,
        "payload": {
            "eta_v3": {
                "consumed": True,
                "source": "real_engine",  # so the report can tell real vs synthetic
                "voice_agree": voice_agree,
                "pm_final": pm_final,
                "engine_live": engine_live,
                "strong_corroboration": strong,
                "verdict_alignment": -1 if direction_conflict else 1,
                "verdict_alignment_label": "CONFLICT" if direction_conflict else "MATCH",
                "base_probability": base_probability,
                "adjusted_probability": adjusted,
                "delta": delta,
                "apex_only_delta": apex_only_delta,
                "gauntlet_delta": gauntlet_delta,
                "gauntlet_weight": gauntlet_weight if gauntlet_delta is not None else 0.0,
                "blend_weight": 0.20,
                "bonus_applied": bonus,
                "penalty_applied": penalty,
                "direction": direction,
                "fire_count": agg["fire_count"],
                "setup_names": agg["setup_names"],
                "n_bars": len(decisions),
                "long_fires": long_fires,
                "short_fires": short_fires,
            },
        },
    }
