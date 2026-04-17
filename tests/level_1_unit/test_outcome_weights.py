"""Tests for outcome-weighted gate recalibration module.

Batch 10A. Covers:
- GateDayRecord creation and immutability
- compute_gate_weights with known correlations
- outcome_weighted_pass_rate vs raw pass_rate
- Edge cases: empty data, all-pass, all-fail, single gate
"""
from __future__ import annotations

import pytest

from mnq.gauntlet.outcome_weights import (
    GateDayRecord,
    OutcomeWeights,
    compute_gate_weights,
    outcome_weighted_pass_rate,
    outcome_weighted_score,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_records_positive_corr() -> list[GateDayRecord]:
    """Gate A passes on profitable days, fails on losing days.

    Strong positive correlation → high weight.
    """
    records = []
    for i in range(50):
        if i < 30:
            # Profitable day, gate A passes
            records.append(GateDayRecord(
                day_idx=i,
                gate_passed={"gate_a": True, "gate_b": True},
                gate_scores={"gate_a": 0.9, "gate_b": 0.8},
                pnl=10.0 + i * 0.1,
            ))
        else:
            # Losing day, gate A fails
            records.append(GateDayRecord(
                day_idx=i,
                gate_passed={"gate_a": False, "gate_b": True},
                gate_scores={"gate_a": 0.2, "gate_b": 0.7},
                pnl=-5.0 - (i - 30) * 0.1,
            ))
    return records


def _make_records_negative_corr() -> list[GateDayRecord]:
    """Gate A passes on LOSING days, fails on profitable days.

    Strong negative correlation → zero weight (pearson_clamp).
    """
    records = []
    for i in range(50):
        if i < 30:
            # Profitable day, gate A fails (anti-correlated)
            records.append(GateDayRecord(
                day_idx=i,
                gate_passed={"gate_a": False, "gate_b": True},
                gate_scores={"gate_a": 0.2, "gate_b": 0.8},
                pnl=10.0 + i * 0.1,
            ))
        else:
            # Losing day, gate A passes (anti-correlated)
            records.append(GateDayRecord(
                day_idx=i,
                gate_passed={"gate_a": True, "gate_b": True},
                gate_scores={"gate_a": 0.9, "gate_b": 0.7},
                pnl=-5.0 - (i - 30) * 0.1,
            ))
    return records


def _make_records_no_corr() -> list[GateDayRecord]:
    """Gate passes and fails regardless of PnL — no correlation."""
    import random
    rng = random.Random(42)
    records = []
    for i in range(100):
        records.append(GateDayRecord(
            day_idx=i,
            gate_passed={"gate_a": rng.random() > 0.5, "gate_b": True},
            gate_scores={"gate_a": rng.random(), "gate_b": 0.8},
            pnl=rng.uniform(-10, 10),
        ))
    return records


# ---------------------------------------------------------------------------
# Tests: GateDayRecord
# ---------------------------------------------------------------------------

class TestGateDayRecord:
    def test_creation(self) -> None:
        r = GateDayRecord(
            day_idx=0,
            gate_passed={"gate_a": True},
            gate_scores={"gate_a": 0.9},
            pnl=5.0,
        )
        assert r.day_idx == 0
        assert r.gate_passed["gate_a"] is True
        assert r.pnl == 5.0

    def test_frozen(self) -> None:
        r = GateDayRecord(
            day_idx=0,
            gate_passed={"gate_a": True},
            gate_scores={"gate_a": 0.9},
            pnl=5.0,
        )
        with pytest.raises(AttributeError):
            r.pnl = 10.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: compute_gate_weights
# ---------------------------------------------------------------------------

class TestComputeGateWeights:
    def test_positive_correlation_high_weight(self) -> None:
        records = _make_records_positive_corr()
        weights = compute_gate_weights(records)
        assert isinstance(weights, OutcomeWeights)
        # gate_a should have high weight (positive corr)
        assert weights.gate_weights["gate_a"] > 0.3

    def test_negative_correlation_zero_weight(self) -> None:
        records = _make_records_negative_corr()
        weights = compute_gate_weights(records)
        # gate_a is anti-correlated → zero weight with pearson_clamp
        assert weights.gate_weights["gate_a"] == 0.0

    def test_negative_correlation_shift_method(self) -> None:
        records = _make_records_negative_corr()
        weights = compute_gate_weights(records, correlation_method="pearson_shift")
        # With shift, even anti-correlated gates get > 0
        assert weights.gate_weights["gate_a"] > 0.0
        # But should be < 0.5 (below neutral)
        assert weights.gate_weights["gate_a"] < 0.5

    def test_always_pass_gate_zero_weight(self) -> None:
        """Gate that always passes has no fail samples → weight 0."""
        records = _make_records_positive_corr()
        # gate_b always passes in positive_corr fixture
        weights = compute_gate_weights(records, min_samples=5)
        # gate_b passes all 50 days, 0 failures → insufficient
        assert weights.gate_weights["gate_b"] == 0.0

    def test_no_correlation_low_weight(self) -> None:
        records = _make_records_no_corr()
        weights = compute_gate_weights(records)
        # Random gate should have ~0 correlation, so low weight
        assert weights.gate_weights["gate_a"] < 0.2

    def test_empty_records(self) -> None:
        weights = compute_gate_weights([])
        assert weights.n_days == 0
        assert weights.gate_weights == {}
        assert weights.gate_results == []

    def test_diagnostics(self) -> None:
        records = _make_records_positive_corr()
        weights = compute_gate_weights(records)
        # Should have results for both gates
        assert len(weights.gate_results) == 2
        gate_a_result = next(r for r in weights.gate_results if r.name == "gate_a")
        assert gate_a_result.pass_count == 30
        assert gate_a_result.fail_count == 20
        assert gate_a_result.pass_pnl_mean > 0
        assert gate_a_result.fail_pnl_mean < 0
        assert gate_a_result.information_value > 0

    def test_n_days_and_total_pnl(self) -> None:
        records = _make_records_positive_corr()
        weights = compute_gate_weights(records)
        assert weights.n_days == 50
        expected_total = sum(r.pnl for r in records)
        assert abs(weights.total_pnl - expected_total) < 0.01

    def test_min_samples_threshold(self) -> None:
        """With high min_samples, gates with few failures get zero weight."""
        records = _make_records_positive_corr()
        weights = compute_gate_weights(records, min_samples=25)
        # gate_a has 20 failures < 25 → insufficient
        assert weights.gate_weights["gate_a"] == 0.0


# ---------------------------------------------------------------------------
# Tests: outcome_weighted_pass_rate
# ---------------------------------------------------------------------------

class TestOutcomeWeightedPassRate:
    def test_all_pass_high_weight(self) -> None:
        gate_passed = {"gate_a": True, "gate_b": True}
        gate_weights = {"gate_a": 1.0, "gate_b": 0.5}
        pr = outcome_weighted_pass_rate(gate_passed, gate_weights)
        assert pr == pytest.approx(1.0)

    def test_mixed_results(self) -> None:
        gate_passed = {"gate_a": True, "gate_b": False}
        gate_weights = {"gate_a": 1.0, "gate_b": 0.5}
        pr = outcome_weighted_pass_rate(gate_passed, gate_weights)
        # weighted: 1.0 * 1 + 0.5 * 0 = 1.0, total = 1.5
        assert pr == pytest.approx(1.0 / 1.5)

    def test_zero_weight_gate_ignored(self) -> None:
        gate_passed = {"gate_a": True, "gate_b": False}
        gate_weights = {"gate_a": 1.0, "gate_b": 0.0}
        pr = outcome_weighted_pass_rate(gate_passed, gate_weights)
        # gate_b ignored, only gate_a counts
        assert pr == pytest.approx(1.0)

    def test_all_zero_weights_returns_neutral(self) -> None:
        gate_passed = {"gate_a": True, "gate_b": False}
        gate_weights = {"gate_a": 0.0, "gate_b": 0.0}
        pr = outcome_weighted_pass_rate(gate_passed, gate_weights)
        assert pr == pytest.approx(0.5)

    def test_empty_gates(self) -> None:
        pr = outcome_weighted_pass_rate({}, {})
        assert pr == pytest.approx(0.5)

    def test_missing_weight_defaults_zero(self) -> None:
        gate_passed = {"gate_a": True, "gate_unknown": False}
        gate_weights = {"gate_a": 1.0}
        pr = outcome_weighted_pass_rate(gate_passed, gate_weights)
        # gate_unknown has no weight → ignored
        assert pr == pytest.approx(1.0)

    def test_high_weight_gate_dominates(self) -> None:
        # gate_a fails with weight 10, gate_b passes with weight 0.1
        gate_passed = {"gate_a": False, "gate_b": True}
        gate_weights = {"gate_a": 10.0, "gate_b": 0.1}
        pr = outcome_weighted_pass_rate(gate_passed, gate_weights)
        # 0 * 10 + 1 * 0.1 = 0.1, total = 10.1
        assert pr == pytest.approx(0.1 / 10.1, abs=0.001)


# ---------------------------------------------------------------------------
# Tests: outcome_weighted_score
# ---------------------------------------------------------------------------

class TestOutcomeWeightedScore:
    def test_weighted_score(self) -> None:
        gate_scores = {"gate_a": 0.8, "gate_b": 0.4}
        gate_weights = {"gate_a": 1.0, "gate_b": 1.0}
        s = outcome_weighted_score(gate_scores, gate_weights)
        assert s == pytest.approx(0.6)

    def test_zero_weights_neutral(self) -> None:
        gate_scores = {"gate_a": 0.8}
        gate_weights = {"gate_a": 0.0}
        s = outcome_weighted_score(gate_scores, gate_weights)
        assert s == pytest.approx(0.5)

    def test_single_gate(self) -> None:
        gate_scores = {"gate_a": 0.9}
        gate_weights = {"gate_a": 0.5}
        s = outcome_weighted_score(gate_scores, gate_weights)
        assert s == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Tests: Integration — full pipeline
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_positive_corr_improves_filtering(self) -> None:
        """When gates are positively correlated, outcome weights should
        produce higher pass rates on profitable days."""
        records = _make_records_positive_corr()
        weights = compute_gate_weights(records)

        profitable_ow_prs = []
        losing_ow_prs = []
        for r in records:
            ow_pr = outcome_weighted_pass_rate(r.gate_passed, weights.gate_weights)
            if r.pnl > 0:
                profitable_ow_prs.append(ow_pr)
            else:
                losing_ow_prs.append(ow_pr)

        # Profitable days should have higher outcome-weighted pass rate
        # than losing days (that's the whole point)
        mean_profit_pr = sum(profitable_ow_prs) / len(profitable_ow_prs)
        mean_loss_pr = sum(losing_ow_prs) / len(losing_ow_prs)
        assert mean_profit_pr > mean_loss_pr

    def test_negative_corr_zeroed_out(self) -> None:
        """When gate_a is anti-correlated, it should get zero weight,
        making outcome-weighted pass rate independent of gate_a."""
        records = _make_records_negative_corr()
        weights = compute_gate_weights(records)

        assert weights.gate_weights["gate_a"] == 0.0
        # Since gate_b always passes and gate_a is zeroed out,
        # all days should have same OW pass rate
        prs = set()
        for r in records:
            pr = outcome_weighted_pass_rate(r.gate_passed, weights.gate_weights)
            prs.add(round(pr, 4))
        # Should be 1 or 2 unique values (0.5 if all zero, or 1.0 if gate_b has weight)
        assert len(prs) <= 2
