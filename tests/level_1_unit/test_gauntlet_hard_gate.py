"""Tests for gauntlet hard-gate — Batch 9A."""
from __future__ import annotations

from mnq.gauntlet.day_aggregate import GauntletDayScore
from mnq.gauntlet.hard_gate import (
    GauntletHardGateConfig,
    combine_gates,
    gauntlet_hard_gate,
)


def _score(
    pass_rate: float = 0.75,
    n_passed: int = 9,
    n_failed: int = 3,
    failed_gates: list[str] | None = None,
) -> GauntletDayScore:
    return GauntletDayScore(
        delta=0.5,
        voice=50.0,
        pass_rate=pass_rate,
        n_passed=n_passed,
        n_failed=n_failed,
        failed_gates=failed_gates or [],
        eval_bar_idx=0,
    )


class TestHardGateBasic:
    def test_full_when_above_reduce_threshold(self) -> None:
        dec = gauntlet_hard_gate(_score(pass_rate=0.75))
        assert dec["action"] == "full"
        assert dec["size_mult"] == 1.0

    def test_full_at_exact_reduce_threshold(self) -> None:
        dec = gauntlet_hard_gate(_score(pass_rate=0.60))
        assert dec["action"] == "full"

    def test_reduced_below_reduce_above_skip(self) -> None:
        dec = gauntlet_hard_gate(_score(pass_rate=0.50))
        assert dec["action"] == "reduced"
        assert dec["size_mult"] == 0.5

    def test_skip_below_skip_threshold(self) -> None:
        dec = gauntlet_hard_gate(_score(pass_rate=0.30))
        assert dec["action"] == "skip"
        assert dec["size_mult"] == 0.0

    def test_skip_at_zero_pass_rate(self) -> None:
        dec = gauntlet_hard_gate(_score(pass_rate=0.0))
        assert dec["action"] == "skip"

    def test_full_at_perfect_pass_rate(self) -> None:
        dec = gauntlet_hard_gate(_score(pass_rate=1.0))
        assert dec["action"] == "full"
        assert dec["size_mult"] == 1.0


class TestCriticalGates:
    def test_critical_gate_failure_forces_skip(self) -> None:
        dec = gauntlet_hard_gate(
            _score(pass_rate=0.90, failed_gates=["gate_regime"]),
        )
        assert dec["action"] == "skip"
        assert "critical" in dec["reason"]

    def test_non_critical_gate_failure_no_skip(self) -> None:
        dec = gauntlet_hard_gate(
            _score(pass_rate=0.75, failed_gates=["gate_session"]),
        )
        assert dec["action"] == "full"

    def test_custom_critical_gates(self) -> None:
        cfg = GauntletHardGateConfig(critical_gates=frozenset({"gate_streak"}))
        dec = gauntlet_hard_gate(
            _score(pass_rate=0.90, failed_gates=["gate_streak"]),
            config=cfg,
        )
        assert dec["action"] == "skip"

    def test_empty_critical_gates_no_check(self) -> None:
        cfg = GauntletHardGateConfig(critical_gates=frozenset())
        dec = gauntlet_hard_gate(
            _score(pass_rate=0.90, failed_gates=["gate_regime"]),
            config=cfg,
        )
        assert dec["action"] == "full"


class TestCustomConfig:
    def test_tight_thresholds(self) -> None:
        cfg = GauntletHardGateConfig(skip_threshold=0.60, reduce_threshold=0.80)
        dec = gauntlet_hard_gate(_score(pass_rate=0.65), config=cfg)
        assert dec["action"] == "reduced"

    def test_very_loose_thresholds(self) -> None:
        cfg = GauntletHardGateConfig(skip_threshold=0.10, reduce_threshold=0.20)
        dec = gauntlet_hard_gate(_score(pass_rate=0.25), config=cfg)
        assert dec["action"] == "full"

    def test_custom_reduced_size(self) -> None:
        cfg = GauntletHardGateConfig(reduced_size=0.25)
        dec = gauntlet_hard_gate(_score(pass_rate=0.50), config=cfg)
        assert dec["action"] == "reduced"
        assert dec["size_mult"] == 0.25


class TestCombineGates:
    def test_apex_skip_wins_over_gauntlet_full(self) -> None:
        apex = {"action": "skip", "size_mult": 0.0, "reason": "apex_dissent"}
        gauntlet = {"action": "full", "size_mult": 1.0, "reason": "gauntlet_ok"}
        combined = combine_gates(apex, gauntlet)
        assert combined["action"] == "skip"
        assert combined["size_mult"] == 0.0

    def test_gauntlet_skip_wins_over_apex_full(self) -> None:
        apex = {"action": "full", "size_mult": 1.0, "reason": "apex_ok"}
        gauntlet = {"action": "skip", "size_mult": 0.0, "reason": "gauntlet_block"}
        combined = combine_gates(apex, gauntlet)
        assert combined["action"] == "skip"
        assert combined["size_mult"] == 0.0
        assert "gauntlet_block" in combined["reason"]

    def test_both_full_returns_full(self) -> None:
        apex = {"action": "full", "size_mult": 1.0, "reason": "apex_ok"}
        gauntlet = {"action": "full", "size_mult": 1.0, "reason": "gauntlet_ok"}
        combined = combine_gates(apex, gauntlet)
        assert combined["action"] == "full"
        assert combined["size_mult"] == 1.0

    def test_reduced_wins_over_full(self) -> None:
        apex = {"action": "reduced", "size_mult": 0.5, "reason": "apex_soft"}
        gauntlet = {"action": "full", "size_mult": 1.0, "reason": "gauntlet_ok"}
        combined = combine_gates(apex, gauntlet)
        assert combined["action"] == "reduced"

    def test_gauntlet_reduced_wins_over_apex_full(self) -> None:
        apex = {"action": "full", "size_mult": 1.0, "reason": "apex_ok"}
        gauntlet = {"action": "reduced", "size_mult": 0.5, "reason": "gauntlet_marginal"}
        combined = combine_gates(apex, gauntlet)
        assert combined["action"] == "reduced"
        assert combined["size_mult"] == 0.5

    def test_both_reduced_takes_smaller_size(self) -> None:
        apex = {"action": "reduced", "size_mult": 0.5, "reason": "apex"}
        gauntlet = {"action": "reduced", "size_mult": 0.25, "reason": "gauntlet"}
        combined = combine_gates(apex, gauntlet)
        assert combined["action"] == "reduced"
        assert combined["size_mult"] == 0.25

    def test_combined_reason_includes_both(self) -> None:
        apex = {"action": "full", "size_mult": 1.0, "reason": "apex_ok"}
        gauntlet = {"action": "skip", "size_mult": 0.0, "reason": "gauntlet_block"}
        combined = combine_gates(apex, gauntlet)
        assert "apex_ok" in combined["reason"]
        assert "gauntlet_block" in combined["reason"]

    def test_both_skip_returns_skip(self) -> None:
        apex = {"action": "skip", "size_mult": 0.0, "reason": "apex_block"}
        gauntlet = {"action": "skip", "size_mult": 0.0, "reason": "gauntlet_block"}
        combined = combine_gates(apex, gauntlet)
        assert combined["action"] == "skip"
        assert combined["size_mult"] == 0.0


class TestOutcomeWeightedHardGate:
    """Batch 10A — outcome-weighted pass_rate in hard-gate."""

    def test_ow_full_when_value_adding_gate_passes(self) -> None:
        """cross_mag passes (high weight) → full despite other failures."""
        # cross_mag is the only value-adding gate
        weights = {"cross_mag": 0.5, "orderflow": 0.0, "regime": 0.0, "trend_align": 0.0}
        cfg = GauntletHardGateConfig(
            skip_threshold=0.40,
            reduce_threshold=0.60,
            critical_gates=frozenset(),
            gate_weights=weights,
        )
        # Day where cross_mag passes but anti-correlated gates fail
        score = _score(
            pass_rate=0.50,  # raw would be "reduced"
            n_passed=6, n_failed=6,
            failed_gates=["orderflow", "regime", "trend_align", "vol_band", "session", "time_of_day"],
        )
        dec = gauntlet_hard_gate(score, config=cfg)
        # cross_mag not in failed_gates → passes, weight 0.5
        # All other weighted gates have weight 0 → ignored
        # OW pass rate = 1.0 (only counted gate passes)
        assert dec["action"] == "full"
        assert "outcome_weighted" in dec["reason"]

    def test_ow_skip_when_value_adding_gate_fails(self) -> None:
        """cross_mag fails (high weight) → skip even with high raw pass_rate."""
        weights = {"cross_mag": 1.0, "session": 0.02, "vol_band": 0.01}
        cfg = GauntletHardGateConfig(
            skip_threshold=0.40,
            reduce_threshold=0.60,
            critical_gates=frozenset(),
            gate_weights=weights,
        )
        score = _score(
            pass_rate=0.83,  # raw would be "full"
            n_passed=10, n_failed=2,
            failed_gates=["cross_mag", "vol_band"],
        )
        dec = gauntlet_hard_gate(score, config=cfg)
        # cross_mag fails (weight 1.0), vol_band fails (weight 0.01)
        # session passes (weight 0.02)
        # OW = 0.02 / (1.0 + 0.02 + 0.01) = 0.019 < skip threshold
        assert dec["action"] == "skip"

    def test_ow_none_falls_back_to_raw(self) -> None:
        """No gate_weights → uses raw pass_rate (backward compat)."""
        cfg = GauntletHardGateConfig(
            skip_threshold=0.40,
            reduce_threshold=0.60,
            critical_gates=frozenset(),
            gate_weights=None,
        )
        score = _score(pass_rate=0.75)
        dec = gauntlet_hard_gate(score, config=cfg)
        assert dec["action"] == "full"
        assert "raw" in dec["reason"]

    def test_ow_reduced_zone(self) -> None:
        """OW pass rate between skip and reduce thresholds → reduced."""
        weights = {"cross_mag": 0.5, "session": 0.5}
        cfg = GauntletHardGateConfig(
            skip_threshold=0.30,
            reduce_threshold=0.70,
            critical_gates=frozenset(),
            gate_weights=weights,
        )
        score = _score(
            pass_rate=0.50,
            n_passed=6, n_failed=6,
            failed_gates=["cross_mag", "orderflow", "regime", "trend_align", "vol_band", "time_of_day"],
        )
        dec = gauntlet_hard_gate(score, config=cfg)
        # cross_mag fails (w=0.5), session passes (w=0.5)
        # OW = 0.5 / 1.0 = 0.50 → between 0.30 and 0.70 → reduced
        assert dec["action"] == "reduced"

    def test_critical_gates_still_override_ow(self) -> None:
        """Critical gate failure still forces skip even with OW."""
        weights = {"cross_mag": 1.0}
        cfg = GauntletHardGateConfig(
            skip_threshold=0.10,
            reduce_threshold=0.20,
            critical_gates=frozenset({"gate_regime"}),
            gate_weights=weights,
        )
        score = _score(
            pass_rate=0.92,
            n_passed=11, n_failed=1,
            failed_gates=["gate_regime"],
        )
        dec = gauntlet_hard_gate(score, config=cfg)
        assert dec["action"] == "skip"
        assert "critical" in dec["reason"]
