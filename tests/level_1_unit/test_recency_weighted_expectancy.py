"""Tests for v0.2.18's recency-weighted expectancy in
``mnq.spec.runtime_payload``.

Pin the contract:

  * Empty / None daily_pnl -> None (not 0.0; preserve "no signal"
    semantics distinct from "zero edge")
  * Recency-weighted equals unweighted when all days have same age
  * Older days contribute exponentially less weight
  * Half-life works: doubling the age halves the weight again
  * Malformed dates are skipped silently (no crash)
  * Zero risk_ticks -> None (no division by zero)
  * The build_spec_payload payload includes the field
"""

from __future__ import annotations

import pytest

from mnq.spec.runtime_payload import (
    DEFAULT_HALF_LIFE_DAYS,
    _recency_weighted_expectancy_r,
    build_spec_payload,
)


def _cfg(*, risk_ticks: int = 40):
    """Make a fake StrategyConfig-ish object."""
    return type("F", (), {"risk_ticks": risk_ticks})()


# ---------------------------------------------------------------------------
# Empty / None inputs
# ---------------------------------------------------------------------------


def test_no_daily_pnl_returns_none() -> None:
    assert _recency_weighted_expectancy_r(_cfg(), None) is None


def test_empty_daily_pnl_returns_none() -> None:
    assert _recency_weighted_expectancy_r(_cfg(), {}) is None


def test_zero_risk_ticks_returns_none() -> None:
    """Defensive: risk_ticks=0 -> None (don't divide by zero)."""
    daily = {"2026-01-01": 100.0}
    cfg = type("F", (), {"risk_ticks": 0})()
    assert _recency_weighted_expectancy_r(cfg, daily) is None


def test_no_risk_ticks_attribute_returns_none() -> None:
    """Defensive: cfg without risk_ticks -> None."""
    daily = {"2026-01-01": 100.0}
    cfg = type("F", (), {})()
    assert _recency_weighted_expectancy_r(cfg, daily) is None


def test_malformed_dates_skipped(monkeypatch) -> None:
    """Malformed date strings in daily_pnl are silently skipped --
    we don't crash, we just compute over the valid dates. If ALL
    dates are malformed, returns None."""
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    # All malformed -> None
    assert (
        _recency_weighted_expectancy_r(
            _cfg(),
            {"not-a-date": 100.0, "also-bad": 50.0},
        )
        is None
    )


def test_one_malformed_one_valid(monkeypatch) -> None:
    """Mixed valid + invalid dates: compute over valid ones only."""
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    daily = {"2026-01-01": 100.0, "garbage": 999.0}
    result = _recency_weighted_expectancy_r(_cfg(), daily)
    # Should compute the valid one with weight 1.0; garbage skipped.
    # weighted_pnl / total_weight = 100 / 1 = 100; / (1.0 * 20) = 5.0
    assert result == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Weighting math
# ---------------------------------------------------------------------------


def test_single_day_weighted_equals_unweighted(monkeypatch) -> None:
    """One day -> total_weight=1.0 -> weighted_pnl_per_day = pnl.
    expectancy_r = pnl / (rate * risk_dollars)."""
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    daily = {"2026-01-01": 100.0}
    cfg = _cfg(risk_ticks=40)  # risk_dollars = 40 * 0.5 = $20
    result = _recency_weighted_expectancy_r(cfg, daily)
    # 100 / (1.0 * 20) = 5.0
    assert result == pytest.approx(5.0)


def test_two_same_day_pnls_use_simple_mean(monkeypatch) -> None:
    """Two days sharing the latest date both have weight 1.0.
    Just kidding -- date keys are unique, so this doesn't apply.
    But two different dates with the same age should weigh equally."""
    # Use ages 0 and exactly 14 days (one half-life): weights 1 + 0.5 = 1.5
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    daily = {
        "2026-01-15": 100.0,  # age 0, weight 1.0
        "2026-01-01": 50.0,  # age 14 days, weight 0.5
    }
    cfg = _cfg(risk_ticks=40)  # risk_dollars = $20
    result = _recency_weighted_expectancy_r(
        cfg,
        daily,
        half_life_days=14.0,
    )
    # weighted_pnl = 100*1 + 50*0.5 = 125
    # total_weight = 1.5
    # pnl_per_day = 125 / 1.5 = 83.33
    # expectancy_r = 83.33 / 20 = 4.166...
    assert result == pytest.approx(125.0 / 1.5 / 20.0, rel=0.01)


def test_recent_day_dominates_old_day(monkeypatch) -> None:
    """A recent +PnL day with a much older -PnL day -> recency
    weighting makes the result lean toward the recent day."""
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    daily = {
        "2026-01-15": 100.0,  # recent winner, weight 1.0
        "2024-01-15": -100.0,  # 730 days back, weight ~0
    }
    cfg = _cfg(risk_ticks=40)
    result = _recency_weighted_expectancy_r(
        cfg,
        daily,
        half_life_days=14.0,
    )
    # Recent day weight ~1, old day weight 0.5^(730/14) ~= 0
    # So weighted ~= 100 / 1 / 20 = 5.0, NOT 0.0 (which would be
    # the unweighted mean of +100 + -100 = 0)
    assert result is not None
    assert result > 4.0  # Strongly leaning to the recent +day


def test_unweighted_returns_zero_when_recency_pulls_to_zero(
    monkeypatch,
) -> None:
    """Edge: equal-magnitude positive recent + negative ancient
    should NOT cancel under recency weighting (recent dominates).
    Pin this explicitly so a future bug that drops the weighting
    fails the test."""
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    # Same-magnitude +100 and -100, but +100 is 2 weeks newer
    daily = {
        "2026-01-15": 100.0,
        "2026-01-01": -100.0,  # exactly one half-life back
    }
    cfg = _cfg(risk_ticks=40)
    result = _recency_weighted_expectancy_r(
        cfg,
        daily,
        half_life_days=14.0,
    )
    # weighted_pnl = 100*1 + (-100)*0.5 = 50
    # total_weight = 1.5
    # pnl_per_day = 50 / 1.5 = 33.33
    # expectancy_r = 33.33 / 20 = 1.666...
    assert result == pytest.approx(50.0 / 1.5 / 20.0, rel=0.01)
    # Sanity: unweighted would be 0
    assert result > 0.5


def test_half_life_changes_result(monkeypatch) -> None:
    """A shorter half-life puts MORE weight on the recent day."""
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    daily = {
        "2026-01-15": 100.0,
        "2026-01-01": -100.0,
    }
    cfg = _cfg(risk_ticks=40)
    short = _recency_weighted_expectancy_r(
        cfg,
        daily,
        half_life_days=3.0,  # very recent-biased
    )
    long = _recency_weighted_expectancy_r(
        cfg,
        daily,
        half_life_days=60.0,  # broader window
    )
    # Short half-life should pull harder toward the recent +100
    assert short > long


# ---------------------------------------------------------------------------
# build_spec_payload integration
# ---------------------------------------------------------------------------


def test_payload_includes_recency_weighted_field() -> None:
    """The Firm-shaped payload must include
    `recency_weighted_expectancy_r` and `recency_half_life_days`.
    Downstream consumers read by key."""
    payload = build_spec_payload("r5_real_wide_target")
    assert "recency_weighted_expectancy_r" in payload
    assert "recency_half_life_days" in payload


def test_payload_recency_field_is_float_when_data_available() -> None:
    """For a variant with cached_backtest provenance, the recency
    field should be a number (not None)."""
    payload = build_spec_payload("r5_real_wide_target")
    if "cached_backtest" in payload["provenance"]:
        assert payload["recency_weighted_expectancy_r"] is not None
        assert isinstance(payload["recency_weighted_expectancy_r"], float)
        assert payload["recency_half_life_days"] == DEFAULT_HALF_LIFE_DAYS


def test_payload_recency_is_none_for_unknown_variant() -> None:
    """Variant without backtest data -> recency field is None
    (preserves 'no signal' semantics)."""
    payload = build_spec_payload("totally_made_up_variant_12345")
    # Variant not in VARIANTS, no daily_pnl -> recency=None
    assert payload["recency_weighted_expectancy_r"] is None
    assert payload["recency_half_life_days"] is None


def test_default_half_life_is_documented_constant() -> None:
    """Pin the constant so a future calibration change is visible
    in git."""
    assert DEFAULT_HALF_LIFE_DAYS == 14.0
