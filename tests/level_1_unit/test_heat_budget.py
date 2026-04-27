"""Tests for mnq.risk.heat_budget — Phase 5 per-regime heat caps."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mnq.risk.heat_budget import (
    CanonicalRegime,
    HeatBudget,
    Position,
    compute_aggregate_heat,
    compute_position_heat,
    heat_budget_gate,
)

# ── Position fixtures ──────────────────────────────────────────────────


def _pos(
    symbol: str = "MNQ",
    qty: int = 1,
    price: float = 20000.0,
    point_value: float = 5.0,
    atr: float = 50.0,
    baseline_atr: float = 50.0,
) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        entry_price=price,
        point_value=point_value,
        current_atr=atr,
        baseline_atr=baseline_atr,
    )


# ── compute_position_heat ──────────────────────────────────────────────


class TestComputePositionHeat:
    def test_basic_heat(self):
        """1 MNQ at 20000, $5/pt, $5k account = notional $100k / $5k = 20.0"""
        heat = compute_position_heat(_pos(), account_equity=5000.0)
        assert heat == pytest.approx(20.0, rel=0.01)

    def test_vol_multiplier_scales(self):
        """Double ATR should double the heat."""
        normal = compute_position_heat(_pos(atr=50, baseline_atr=50), account_equity=5000.0)
        doubled = compute_position_heat(_pos(atr=100, baseline_atr=50), account_equity=5000.0)
        assert doubled == pytest.approx(normal * 2.0, rel=0.01)

    def test_vol_multiplier_capped(self):
        """Vol multiplier shouldn't exceed cap."""
        capped = compute_position_heat(
            _pos(atr=500, baseline_atr=50),
            account_equity=5000.0,
            vol_multiplier_cap=2.0,
        )
        normal = compute_position_heat(_pos(atr=50, baseline_atr=50), account_equity=5000.0)
        assert capped == pytest.approx(normal * 2.0, rel=0.01)

    def test_zero_equity_returns_max(self):
        heat = compute_position_heat(_pos(), account_equity=0.0)
        assert heat == 1.0

    def test_zero_atr_no_crash(self):
        heat = compute_position_heat(_pos(atr=0, baseline_atr=0), account_equity=5000.0)
        assert heat > 0  # Uses vol_mult = 1.0 fallback


# ── compute_aggregate_heat ─────────────────────────────────────────────


class TestComputeAggregateHeat:
    def test_empty_positions(self):
        assert compute_aggregate_heat([], 5000.0) == 0.0

    def test_single_position_no_correlation_penalty(self):
        """Single position: aggregate = individual."""
        pos = _pos()
        individual = compute_position_heat(pos, 5000.0)
        aggregate = compute_aggregate_heat([pos], 5000.0)
        assert aggregate == pytest.approx(individual, rel=0.01)

    def test_correlated_positions_add_penalty(self):
        """Two correlated positions should have higher aggregate than sum."""
        p1 = _pos("MNQ", qty=1)
        p2 = _pos("MES", qty=1, price=5000.0)
        individual_sum = compute_position_heat(p1, 5000.0) + compute_position_heat(p2, 5000.0)
        aggregate = compute_aggregate_heat([p1, p2], 5000.0)
        assert aggregate > individual_sum  # Correlation penalty


# ── HeatBudget ─────────────────────────────────────────────────────────


class TestHeatBudget:
    def test_dead_zone_always_denies(self):
        budget = HeatBudget(regime=CanonicalRegime.DEAD_ZONE)
        result = budget.check(_pos(), [], 5000.0)
        assert not result.allow
        assert "Dead-zone" in result.reason

    def test_transition_conservative(self):
        budget = HeatBudget(regime=CanonicalRegime.TRANSITION)
        # Transition allows max 1 concurrent
        assert budget.config.max_concurrent == 1

    def test_low_vol_trend_generous(self):
        budget = HeatBudget(regime=CanonicalRegime.LOW_VOL_TREND)
        assert budget.config.max_heat == 0.8
        assert budget.config.max_concurrent == 3

    def test_concurrency_cap_blocks(self):
        budget = HeatBudget(regime=CanonicalRegime.TRANSITION)
        existing = [_pos()]  # 1 position already, cap = 1
        result = budget.check(_pos(), existing, 100000.0)  # Large equity
        assert not result.allow
        assert "Concurrency cap" in result.reason

    def test_update_regime(self):
        budget = HeatBudget(regime=CanonicalRegime.DEAD_ZONE)
        assert budget.config.max_heat == 0.0
        budget.update_regime("low-vol-trend")
        assert budget.config.max_heat == 0.8

    def test_unknown_regime_falls_back_to_transition(self):
        budget = HeatBudget(regime="unknown-regime")
        assert budget.regime == CanonicalRegime.TRANSITION

    def test_save_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heat.json"
            budget = HeatBudget(
                regime=CanonicalRegime.LOW_VOL_TREND,
                state_path=path,
            )
            budget.save_state([], 5000.0)
            data = json.loads(path.read_text())
            assert data["regime"] == "low-vol-trend"
            assert data["current_heat"] == 0.0


# ── heat_budget_gate ───────────────────────────────────────────────────


class TestHeatBudgetGate:
    def test_gate_returns_gate_result(self):
        budget = HeatBudget(regime=CanonicalRegime.LOW_VOL_TREND)
        # MNQ notional = 20000 * 5.0 = $100k; need equity > 100k/0.8 = $125k
        result = heat_budget_gate(budget, _pos(), [], 200000.0)
        assert result.gate == "heat_budget"
        assert result.allow  # Large equity, should be within budget

    def test_gate_denies_in_dead_zone(self):
        budget = HeatBudget(regime=CanonicalRegime.DEAD_ZONE)
        result = heat_budget_gate(budget, _pos(), [], 5000.0)
        assert not result.allow
