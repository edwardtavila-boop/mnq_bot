"""Tests for ``mnq.spec.runtime_payload._regime_expectancy_stats`` --
v0.2.13's per-regime aggregate stats.

Pin the contract:

  * Empty / None daily_pnl -> empty dict (no per-regime evidence)
  * No tape / classifier unavailable -> empty dict
  * Stats are computed per regime: n_days, total_pnl, pnl_per_day,
    expectancy_r
  * Days with the same regime aggregate together
  * The Firm-payload key is "regime_expectancy" and lives alongside
    "regimes_approved" (key contract)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnq.spec.runtime_payload import (
    _regime_expectancy_stats,
    build_spec_payload,
)

# ---------------------------------------------------------------------------
# build_spec_payload integration
# ---------------------------------------------------------------------------


def test_payload_contains_regime_expectancy_key() -> None:
    """The Firm-shaped payload MUST include ``regime_expectancy`` --
    downstream consumers (PM agent, dashboards) read it by key."""
    payload = build_spec_payload("r5_real_wide_target")
    assert "regime_expectancy" in payload
    assert isinstance(payload["regime_expectancy"], dict)


def test_regime_expectancy_keys_match_classification() -> None:
    """For r5_real_wide_target, the regime_expectancy keys should be
    a subset of the canonical regime labels."""
    payload = build_spec_payload("r5_real_wide_target")
    valid = {
        "low-vol-trend",
        "low-vol-range",
        "low-vol-reversal",
        "high-vol-trend",
        "high-vol-range",
        "high-vol-reversal",
        "crash",
        "euphoria",
        "dead-zone",
        "transition",
    }
    for regime in payload["regime_expectancy"]:
        assert regime in valid, f"unexpected regime key: {regime}"


def test_regime_expectancy_each_entry_has_required_fields() -> None:
    """Each per-regime stats dict must have n_days, total_pnl,
    pnl_per_day, expectancy_r. Missing fields would break the
    Firm payload schema."""
    payload = build_spec_payload("r5_real_wide_target")
    required = {"n_days", "total_pnl", "pnl_per_day", "expectancy_r"}
    for regime, stats in payload["regime_expectancy"].items():
        missing = required - set(stats.keys())
        assert not missing, f"regime {regime} missing {missing}"


# ---------------------------------------------------------------------------
# _regime_expectancy_stats direct -- no daily data / no tape
# ---------------------------------------------------------------------------


def test_no_daily_pnl_yields_empty_dict() -> None:
    """None daily_pnl -> empty dict (no evidence)."""
    cfg = type("F", (), {"risk_ticks": 40, "rr": 2.0})()
    assert _regime_expectancy_stats(cfg, None) == {}


def test_empty_daily_pnl_yields_empty_dict() -> None:
    cfg = type("F", (), {"risk_ticks": 40})()
    assert _regime_expectancy_stats(cfg, {}) == {}


def test_no_tape_yields_empty_dict(monkeypatch, tmp_path: Path) -> None:
    """If the tape is unavailable, the helper falls back to empty.
    The downstream payload sees an empty dict (no per-regime evidence)
    rather than None or a partially populated dict."""
    # Force the per_day_regime_map to return None
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._per_day_regime_map",
        lambda: None,
    )
    cfg = type("F", (), {"risk_ticks": 40})()
    daily = {"2026-01-01": 50.0, "2026-01-02": -20.0}
    result = _regime_expectancy_stats(cfg, daily)
    assert result == {}


# ---------------------------------------------------------------------------
# Aggregation with synthetic regime map
# ---------------------------------------------------------------------------


def test_days_aggregate_per_regime(monkeypatch) -> None:
    """Days with the same regime sum into one entry."""
    fake_regimes = {
        "2026-01-01": "low-vol-trend",
        "2026-01-02": "low-vol-trend",
        "2026-01-03": "high-vol-range",
    }
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._per_day_regime_map",
        lambda: fake_regimes,
    )
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    cfg = type("F", (), {"risk_ticks": 40})()  # risk_dollars = 40 * 0.5 = $20
    daily = {
        "2026-01-01": 100.0,
        "2026-01-02": 50.0,
        "2026-01-03": -30.0,
    }
    result = _regime_expectancy_stats(cfg, daily)
    assert "low-vol-trend" in result
    assert result["low-vol-trend"]["n_days"] == 2.0
    assert result["low-vol-trend"]["total_pnl"] == 150.0
    assert result["low-vol-trend"]["pnl_per_day"] == 75.0
    # expectancy_r = 75 / (1.0 trades_per_day * 20 risk_dollars) = 3.75
    assert result["low-vol-trend"]["expectancy_r"] == pytest.approx(3.75)
    assert result["high-vol-range"]["n_days"] == 1.0
    assert result["high-vol-range"]["total_pnl"] == -30.0


def test_zero_risk_dollars_yields_zero_expectancy(monkeypatch) -> None:
    """Defensive: a misconfigured cfg without risk_ticks yields 0
    expectancy without dividing by zero."""
    fake_regimes = {"2026-01-01": "low-vol-trend"}
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._per_day_regime_map",
        lambda: fake_regimes,
    )
    cfg = type("F", (), {})()  # no risk_ticks attribute
    result = _regime_expectancy_stats(cfg, {"2026-01-01": 100.0})
    assert result["low-vol-trend"]["expectancy_r"] == 0.0


def test_dates_outside_tape_coverage_skipped(monkeypatch) -> None:
    """Dates not in the per-day map are silently skipped (tape window
    may not cover the cached backtest date range)."""
    fake_regimes = {"2026-01-01": "low-vol-trend"}  # only 1 date
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._per_day_regime_map",
        lambda: fake_regimes,
    )
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    cfg = type("F", (), {"risk_ticks": 40})()
    daily = {
        "2026-01-01": 100.0,  # in map
        "2026-01-02": 200.0,  # NOT in map -- skipped
    }
    result = _regime_expectancy_stats(cfg, daily)
    # Only low-vol-trend should appear
    assert set(result.keys()) == {"low-vol-trend"}
    # And only with the in-coverage day
    assert result["low-vol-trend"]["n_days"] == 1.0


def test_per_regime_consistency_with_top_level(monkeypatch) -> None:
    """Sum of per-regime total_pnl should equal total daily P&L
    (when all dates are in the per-day map)."""
    fake_regimes = {
        "2026-01-01": "low-vol-trend",
        "2026-01-02": "low-vol-range",
        "2026-01-03": "low-vol-trend",
    }
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._per_day_regime_map",
        lambda: fake_regimes,
    )
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: 1.0,
    )
    cfg = type("F", (), {"risk_ticks": 40})()
    daily = {
        "2026-01-01": 100.0,
        "2026-01-02": -50.0,
        "2026-01-03": 30.0,
    }
    result = _regime_expectancy_stats(cfg, daily)
    total_per_regime = sum(s["total_pnl"] for s in result.values())
    assert total_per_regime == pytest.approx(sum(daily.values()))
