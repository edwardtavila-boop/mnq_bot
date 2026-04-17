"""Tests for mnq.gauntlet.gates.gate_turnover."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl

from mnq.gauntlet.gates.gate_turnover import TurnoverConfig, run_gate_15


@dataclass
class _Path:
    trades_df: Any


def _path_with_trades(n_trades: int, n_days: int) -> _Path:
    """Build a fake CPCV path with evenly-spaced trades across n_days."""
    ts: list[datetime] = []
    # Distribute trades across the sessions roughly equally.
    per_day = max(1, n_trades // max(n_days, 1))
    remainder = n_trades - per_day * n_days if n_days > 0 else 0
    for d in range(n_days):
        base = datetime(2026, 1, 5, 14, 30, tzinfo=UTC) + timedelta(days=d)
        count = per_day + (1 if d < remainder else 0)
        for k in range(count):
            ts.append(base + timedelta(minutes=k * 5))
    return _Path(trades_df=pl.DataFrame({"entry_ts": ts}))


def test_turnover_within_band_passes():
    cfg = TurnoverConfig(min_trades_per_day=3.0, max_trades_per_day=50.0)
    paths = [_path_with_trades(n_trades=20, n_days=2) for _ in range(5)]  # 10/day
    result = run_gate_15(paths, config=cfg)
    assert result.passed
    assert result.failure_reason is None
    assert 9.0 <= result.metric_values["median_trades_per_day"] <= 11.0


def test_turnover_too_thin_fails():
    cfg = TurnoverConfig(min_trades_per_day=5.0, max_trades_per_day=50.0)
    paths = [_path_with_trades(n_trades=2, n_days=5) for _ in range(5)]  # 0.4/day
    result = run_gate_15(paths, config=cfg)
    assert not result.passed
    assert result.failure_reason is not None
    assert "too thin" in result.failure_reason.lower() or "< min" in result.failure_reason


def test_turnover_too_chatty_fails():
    cfg = TurnoverConfig(min_trades_per_day=3.0, max_trades_per_day=10.0)
    paths = [_path_with_trades(n_trades=100, n_days=2) for _ in range(5)]  # 50/day
    result = run_gate_15(paths, config=cfg)
    assert not result.passed
    assert result.failure_reason is not None
    assert "overtrading" in result.failure_reason.lower() or "> max" in result.failure_reason


def test_empty_cpcv_results_handled():
    result = run_gate_15([], config=TurnoverConfig())
    # With no paths, median is 0 which fails the min-threshold guard.
    assert not result.passed
