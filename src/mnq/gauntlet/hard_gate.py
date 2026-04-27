"""Gauntlet hard-gate — direct block/reduce based on pass_rate.

Batch 9A. The V16 delta-blend path (Batch 5D) proved mathematically
disconnected from the apex_gate thresholds: PM base delta for GO verdicts
always lands above the skip threshold (−0.10), so the gauntlet's ±0.06
nudge never flips a day from allow→block.

This module provides a **parallel** gate that operates on the gauntlet's
raw pass_rate and weighted score rather than blending into the delta. It
sits alongside ``apex_gate`` — callers combine both decisions and take
the stricter one.

Gate logic:

    pass_rate < skip_threshold     → SKIP   (too many gates failed)
    pass_rate < reduce_threshold   → REDUCE (marginal conditions)
    pass_rate >= reduce_threshold  → FULL   (gauntlet confirms)

An optional ``critical_gates`` check allows specific named gates to
force a skip regardless of the overall pass_rate (e.g., regime gate
failing should always block).

**Batch 10A**: Added outcome-weighted mode. When ``gate_weights`` is
provided, the hard-gate uses outcome-weighted pass_rate instead of raw
pass_rate. This filters based on gates that actually predict profitability
rather than gates calibrated against "suitable conditions."

Usage:

    from mnq.gauntlet.hard_gate import gauntlet_hard_gate, combine_gates
    from mnq.eta_v3.gate import apex_gate

    g_decision = gauntlet_hard_gate(day_score)
    a_decision = apex_gate(pm_output)
    final = combine_gates(a_decision, g_decision)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mnq.gauntlet.day_aggregate import GauntletDayScore

__all__ = [
    "GauntletHardGateConfig",
    "HardGateDecision",
    "combine_gates",
    "gauntlet_hard_gate",
]


@dataclass(frozen=True, slots=True)
class GauntletHardGateConfig:
    """Configuration for the gauntlet hard-gate.

    Attributes:
        skip_threshold: Block the trade entirely if pass_rate is below this.
        reduce_threshold: Reduce size if pass_rate is between skip and reduce.
        critical_gates: Gate names that force a skip if they fail,
            regardless of overall pass_rate.
        reduced_size: Size multiplier for reduced action.
        gate_weights: Optional outcome-weighted gate weights (Batch 10A).
            When provided, the hard-gate uses outcome_weighted_pass_rate
            instead of raw pass_rate. Keys are gate names, values are
            weights in [0, 1]. Gates with zero weight are ignored.
    """

    skip_threshold: float = 0.40  # < 5/12 gates → skip
    reduce_threshold: float = 0.60  # < 8/12 gates → reduced
    critical_gates: frozenset[str] = frozenset({"gate_regime"})
    reduced_size: float = 0.5
    gate_weights: dict[str, float] | None = None


# Re-use the same shape as apex_gate's GateDecision
HardGateDecision = dict[str, Any]


def gauntlet_hard_gate(
    day_score: GauntletDayScore,
    *,
    config: GauntletHardGateConfig | None = None,
) -> HardGateDecision:
    """Evaluate a day's gauntlet score through the hard-gate.

    Parameters
    ----------
    day_score:
        Result of ``gauntlet_day_score(bars)`` for this day.
    config:
        Gate thresholds. Uses defaults if None.

    Returns
    -------
    HardGateDecision:
        {"action": "full|reduced|skip", "size_mult": float, "reason": str}
    """
    cfg = config or GauntletHardGateConfig()

    # Critical gate check — named gates that must pass
    if cfg.critical_gates and day_score.failed_gates:
        critical_failures = cfg.critical_gates & set(day_score.failed_gates)
        if critical_failures:
            return _decision(
                "skip",
                0.0,
                f"gauntlet_critical_gate_failed={','.join(sorted(critical_failures))}",
            )

    # Pass-rate — use outcome-weighted if gate_weights provided
    if cfg.gate_weights:
        from mnq.gauntlet.outcome_weights import outcome_weighted_pass_rate

        gate_passed = {
            name: name not in day_score.failed_gates for name in list(cfg.gate_weights.keys())
        }
        # Also include gates that aren't in weights (they'll get 0 weight)
        for name in day_score.failed_gates:
            if name not in gate_passed:
                gate_passed[name] = False
        pr = outcome_weighted_pass_rate(gate_passed, cfg.gate_weights)
        pr_label = "outcome_weighted"
    else:
        pr = day_score.pass_rate
        pr_label = "raw"

    if pr < cfg.skip_threshold:
        return _decision(
            "skip",
            0.0,
            f"gauntlet_hard_skip_{pr_label}={pr:.3f}<{cfg.skip_threshold:.2f}",
        )

    if pr < cfg.reduce_threshold:
        return _decision(
            "reduced",
            cfg.reduced_size,
            f"gauntlet_hard_reduce_{pr_label}={pr:.3f}<{cfg.reduce_threshold:.2f}",
        )

    return _decision(
        "full",
        1.0,
        f"gauntlet_hard_full_{pr_label}={pr:.3f}>={cfg.reduce_threshold:.2f}",
    )


def combine_gates(
    apex_decision: dict[str, Any],
    gauntlet_decision: dict[str, Any],
) -> dict[str, Any]:
    """Combine apex_gate and gauntlet_hard_gate — take the stricter decision.

    Strictness ordering: skip > reduced > full.
    When both are "reduced", take the smaller size_mult.
    When one is "skip", skip wins.

    The combined reason includes both gate reasons for auditability.
    """
    _rank = {"skip": 0, "reduced": 1, "full": 2}

    a_action = apex_decision.get("action", "full")
    g_action = gauntlet_decision.get("action", "full")

    a_rank = _rank.get(a_action, 2)
    g_rank = _rank.get(g_action, 2)

    if a_rank < g_rank:
        # Apex is stricter
        return _decision(
            a_action,
            apex_decision.get("size_mult", 0.0),
            f"apex={apex_decision.get('reason', '')}|gauntlet={gauntlet_decision.get('reason', '')}",
        )
    if g_rank < a_rank:
        # Gauntlet is stricter
        return _decision(
            g_action,
            gauntlet_decision.get("size_mult", 0.0),
            f"gauntlet={gauntlet_decision.get('reason', '')}|apex={apex_decision.get('reason', '')}",
        )

    # Same action — take smaller size_mult
    a_size = float(apex_decision.get("size_mult", 1.0))
    g_size = float(gauntlet_decision.get("size_mult", 1.0))
    return _decision(
        a_action,
        min(a_size, g_size),
        f"both_{a_action}|apex={apex_decision.get('reason', '')}|gauntlet={gauntlet_decision.get('reason', '')}",
    )


def _decision(action: str, size_mult: float, reason: str) -> HardGateDecision:
    return {"action": action, "size_mult": float(size_mult), "reason": reason}
