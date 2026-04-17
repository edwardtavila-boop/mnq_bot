"""Outcome-weighted gate recalibration — train gate weights against realized P&L.

Batch 10A. The gauntlet's 12 gates were calibrated against "conditions
suitable for trading," not against actual profitability. Batch 9B showed
gates are anti-correlated with profit: reduced days avg $1.27 vs full
days avg $0.70.

This module computes per-gate outcome weights by measuring each gate's
pass/fail correlation with realized P&L. Gates that predict profit get
higher weight; gates that are anti-correlated get zero or inverted weight.

The outcome-weighted score replaces raw pass_rate in the hard-gate,
so filtering decisions are driven by actual PnL data instead of
theory-based gate design.

Usage:

    from mnq.gauntlet.outcome_weights import (
        GateDayRecord,
        compute_gate_weights,
        outcome_weighted_pass_rate,
    )

    records = [...]  # GateDayRecord per day
    weights = compute_gate_weights(records)
    # Use weights.weighted_pass_rate(gate_verdicts) instead of raw pass_rate
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "GateDayRecord",
    "GateWeightResult",
    "compute_gate_weights",
    "outcome_weighted_pass_rate",
]


@dataclass(frozen=True, slots=True)
class GateDayRecord:
    """One day's gate verdicts paired with realized PnL.

    Attributes:
        day_idx: Day identifier (for traceability).
        gate_passed: Dict mapping gate name → bool (True = passed).
        gate_scores: Dict mapping gate name → float score [0, 1].
        pnl: Realized PnL for the day in dollars.
    """

    day_idx: int
    gate_passed: dict[str, bool]
    gate_scores: dict[str, float]
    pnl: float


@dataclass(frozen=True, slots=True)
class GateWeightResult:
    """Per-gate outcome weight with diagnostic stats.

    Attributes:
        name: Gate name.
        weight: Outcome weight (0.0 = no value, 1.0 = max value).
            Derived from PnL correlation, clamped to [0, 1].
        raw_correlation: Pearson correlation between gate pass/fail and PnL.
        pass_pnl_mean: Mean PnL on days this gate passed.
        fail_pnl_mean: Mean PnL on days this gate failed.
        pass_count: Number of days gate passed.
        fail_count: Number of days gate failed.
        information_value: How much PnL-discrimination this gate provides.
            Higher = more useful for filtering.
    """

    name: str
    weight: float
    raw_correlation: float
    pass_pnl_mean: float
    fail_pnl_mean: float
    pass_count: int
    fail_count: int
    information_value: float


@dataclass(frozen=True, slots=True)
class OutcomeWeights:
    """Complete set of outcome-weighted gate weights.

    Attributes:
        gate_weights: Mapping of gate name → outcome weight [0, 1].
        gate_results: Full diagnostic results per gate.
        n_days: Number of training days used.
        total_pnl: Total PnL across all training days.
    """

    gate_weights: dict[str, float]
    gate_results: list[GateWeightResult]
    n_days: int
    total_pnl: float
    method: str = "pearson_clamp"


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Compute Pearson correlation coefficient between two series."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return cov / (sx * sy)


def _information_value(
    pass_pnl: list[float], fail_pnl: list[float],
) -> float:
    """Compute information value — how much PnL-separation the gate provides.

    Uses absolute difference of means, normalized by overall stdev.
    Higher IV = more discriminating gate.
    """
    if not pass_pnl or not fail_pnl:
        return 0.0
    all_pnl = pass_pnl + fail_pnl
    overall_mean = sum(all_pnl) / len(all_pnl)
    overall_std = (sum((x - overall_mean) ** 2 for x in all_pnl) / len(all_pnl)) ** 0.5
    if overall_std < 1e-12:
        return 0.0
    pass_mean = sum(pass_pnl) / len(pass_pnl)
    fail_mean = sum(fail_pnl) / len(fail_pnl)
    return abs(pass_mean - fail_mean) / overall_std


def compute_gate_weights(
    records: list[GateDayRecord],
    *,
    min_samples: int = 10,
    correlation_method: str = "pearson_clamp",
) -> OutcomeWeights:
    """Compute outcome-weighted gate weights from training data.

    For each gate, computes Pearson correlation between pass/fail (1/0)
    and realized PnL. Then transforms correlation into a weight:

    - **pearson_clamp** (default): weight = max(0, correlation).
      Anti-correlated gates get zero weight. Positively correlated gates
      get weight proportional to their correlation strength.

    - **pearson_shift**: weight = (correlation + 1) / 2.
      Maps [-1, +1] → [0, 1]. Anti-correlated gates get low-but-nonzero
      weight. Use this if you believe anti-correlated gates still carry
      some information.

    Gates with fewer than ``min_samples`` in either pass or fail bucket
    get weight 0.0 (insufficient data to judge).

    Parameters
    ----------
    records:
        List of GateDayRecord objects, one per day.
    min_samples:
        Minimum number of days a gate must have in both pass and fail
        buckets to compute a meaningful correlation.
    correlation_method:
        How to transform correlation into weight.

    Returns
    -------
    OutcomeWeights with per-gate weights and diagnostics.
    """
    if not records:
        return OutcomeWeights(
            gate_weights={}, gate_results=[], n_days=0, total_pnl=0.0,
            method=correlation_method,
        )

    # Collect all gate names across records
    all_gates: set[str] = set()
    for r in records:
        all_gates.update(r.gate_passed.keys())

    total_pnl = sum(r.pnl for r in records)
    results: list[GateWeightResult] = []

    for gate_name in sorted(all_gates):
        pass_pnl: list[float] = []
        fail_pnl: list[float] = []
        binary_series: list[float] = []
        pnl_series: list[float] = []

        for r in records:
            passed = r.gate_passed.get(gate_name)
            if passed is None:
                continue  # gate not evaluated for this day
            pnl_series.append(r.pnl)
            if passed:
                pass_pnl.append(r.pnl)
                binary_series.append(1.0)
            else:
                fail_pnl.append(r.pnl)
                binary_series.append(0.0)

        # Insufficient data — can't judge
        if len(pass_pnl) < min_samples or len(fail_pnl) < min_samples:
            results.append(GateWeightResult(
                name=gate_name,
                weight=0.0,
                raw_correlation=0.0,
                pass_pnl_mean=_safe_mean(pass_pnl),
                fail_pnl_mean=_safe_mean(fail_pnl),
                pass_count=len(pass_pnl),
                fail_count=len(fail_pnl),
                information_value=0.0,
            ))
            continue

        corr = _pearson(binary_series, pnl_series)
        iv = _information_value(pass_pnl, fail_pnl)

        weight = (corr + 1.0) / 2.0 if correlation_method == "pearson_shift" else max(0.0, corr)

        results.append(GateWeightResult(
            name=gate_name,
            weight=weight,
            raw_correlation=corr,
            pass_pnl_mean=_safe_mean(pass_pnl),
            fail_pnl_mean=_safe_mean(fail_pnl),
            pass_count=len(pass_pnl),
            fail_count=len(fail_pnl),
            information_value=iv,
        ))

    gate_weights = {r.name: r.weight for r in results}

    return OutcomeWeights(
        gate_weights=gate_weights,
        gate_results=results,
        n_days=len(records),
        total_pnl=total_pnl,
        method=correlation_method,
    )


def outcome_weighted_pass_rate(
    gate_passed: dict[str, bool],
    gate_weights: dict[str, float],
) -> float:
    """Compute outcome-weighted pass rate.

    Instead of raw pass_rate = n_passed / n_total, this weights each
    gate's contribution by its outcome weight:

        weighted_pass_rate = sum(weight_i * passed_i) / sum(weight_i)

    Gates with zero weight are effectively ignored.
    If all weights are zero, returns 0.5 (neutral — no information).

    Parameters
    ----------
    gate_passed:
        Dict mapping gate name → bool (True = passed).
    gate_weights:
        Dict mapping gate name → outcome weight [0, 1].

    Returns
    -------
    Weighted pass rate in [0.0, 1.0].
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for gate_name, passed in gate_passed.items():
        w = gate_weights.get(gate_name, 0.0)
        if w <= 0.0:
            continue
        total_weight += w
        if passed:
            weighted_sum += w

    if total_weight < 1e-12:
        return 0.5  # no information — neutral

    return weighted_sum / total_weight


def outcome_weighted_score(
    gate_scores: dict[str, float],
    gate_weights: dict[str, float],
) -> float:
    """Compute outcome-weighted score using continuous gate scores.

    Like ``outcome_weighted_pass_rate`` but uses the gate's continuous
    score [0, 1] instead of binary pass/fail.

    Returns weighted score in [0.0, 1.0].
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for gate_name, score in gate_scores.items():
        w = gate_weights.get(gate_name, 0.0)
        if w <= 0.0:
            continue
        total_weight += w
        weighted_sum += w * score

    if total_weight < 1e-12:
        return 0.5

    return weighted_sum / total_weight


def _safe_mean(xs: list[float]) -> float:
    """Mean that returns 0.0 for empty lists."""
    return sum(xs) / len(xs) if xs else 0.0
