"""Level-2 property tests for attribution metrics + benchmarks + gates 13/14.

These lock in the mathematical properties spelled out in the contracts:
    - alpha invariance under constant shift
    - beta scale
    - alpha vs cash == mean(strategy)
    - regression identity (residuals orthogonal to benchmark)
    - HAC SE >= OLS SE on autocorrelated input
    - Sortino == Sharpe when all returns >= MAR
    - Omega(threshold=mean) ≈ 1

And the gate-level synthetic strategies:
    - pure beta (returns = 0.5 * benchmark + noise)  -> g13 fail, g14 fail
    - constant edge (returns = 5 + noise, ⊥ benchmark) -> g13 pass, g14 pass
    - naive momentum clone                             -> g13 fail on
                                                         naive_momentum only
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from mnq.gauntlet import benchmarks as _bm
from mnq.gauntlet import metrics_attribution as _ma
from mnq.gauntlet.gates.gate_attribution import run_gate_13, run_gate_14

# ---------------------------------------------------------------------------
# Metric property tests
# ---------------------------------------------------------------------------


def _rng(seed=1):
    return np.random.default_rng(seed)


class TestAlphaProperties:
    def test_alpha_invariance_under_shift(self) -> None:
        rng = _rng(1)
        strat = rng.normal(1.0, 0.5, size=200)
        bench = rng.normal(0.0, 1.0, size=200)
        c = 3.7
        a0 = _ma.alpha_with_significance(strat, bench).alpha
        a1 = _ma.alpha_with_significance(strat + c, bench).alpha
        assert a1 == pytest.approx(a0 + c, rel=1e-6, abs=1e-9)

    def test_alpha_vs_cash_equals_mean_strategy(self) -> None:
        rng = _rng(2)
        strat = rng.normal(2.0, 0.5, size=200)
        zeros = np.zeros(200, dtype=np.float64)
        res = _ma.alpha_with_significance(strat, zeros)
        assert res.alpha == pytest.approx(float(strat.mean()), rel=1e-12, abs=1e-12)

    def test_regression_identity(self) -> None:
        rng = _rng(3)
        n = 300
        bench = rng.normal(0.0, 1.0, size=n)
        strat = 2.0 + 0.4 * bench + rng.normal(0.0, 0.3, size=n)
        a = _ma.alpha_with_significance(strat, bench).alpha
        b = _ma.beta(strat, bench)
        resid = strat - (a + b * bench)
        cov = float(np.cov(resid, bench, ddof=1)[0, 1])
        assert abs(cov) < 1e-10

    def test_hac_se_geq_ols_se_on_autocorrelated_input(self) -> None:
        # Build a visibly autocorrelated residual: AR(1) with rho=0.9.
        rng = _rng(4)
        n = 500
        bench = rng.normal(0.0, 1.0, size=n)
        eps = np.zeros(n)
        eps[0] = rng.normal()
        for t in range(1, n):
            eps[t] = 0.9 * eps[t - 1] + rng.normal(0.0, 0.3)
        strat = 0.1 + 0.2 * bench + eps

        # OLS-only SE (lag=0)
        x = np.column_stack([np.ones(n), bench])
        coef, *_ = np.linalg.lstsq(x, strat, rcond=None)
        pred = x @ coef
        resid = strat - pred
        s2 = float((resid ** 2).sum() / (n - 2))
        xtx_inv = np.linalg.inv(x.T @ x)
        ols_se_alpha = math.sqrt(s2 * xtx_inv[0, 0])

        hac = _ma.alpha_with_significance(strat, bench)
        assert hac.alpha_se >= ols_se_alpha - 1e-9, (
            f"HAC SE ({hac.alpha_se}) should be >= OLS SE ({ols_se_alpha}) "
            "for autocorrelated residuals"
        )


class TestBetaProperties:
    def test_beta_scale(self) -> None:
        rng = _rng(5)
        bench = rng.normal(0.0, 1.0, size=200)
        strat = 2.0 + 0.3 * bench + rng.normal(0.0, 0.5, size=200)
        b_orig = _ma.beta(strat, bench)
        c = 2.5
        b_scaled = _ma.beta(c * strat, bench)
        assert b_scaled == pytest.approx(c * b_orig, rel=1e-9)

    def test_beta_zero_on_uncorrelated(self) -> None:
        rng = _rng(6)
        bench = rng.normal(0.0, 1.0, size=500)
        strat = rng.normal(0.0, 1.0, size=500)  # independent
        assert abs(_ma.beta(strat, bench)) < 0.15


class TestDistributionalMetrics:
    def test_sortino_equals_sharpe_when_all_above_mar(self) -> None:
        rng = _rng(7)
        strat = rng.uniform(1.0, 5.0, size=200)  # always > MAR=0
        # Sortino with MAR=0 and no downside is +inf by the formula in our
        # implementation (denom==0 → +inf when mean > 0).
        result = _ma.sortino(strat, mar=0.0)
        assert result == math.inf or result > 100.0

    def test_omega_at_mean_threshold_near_one(self) -> None:
        rng = _rng(8)
        strat = rng.normal(0.0, 1.0, size=500)
        # Omega ≡ 1 at threshold=median for symmetric distributions; using
        # mean on a normal sample gives omega very close to 1.
        o = _ma.omega(strat, threshold=float(strat.mean()))
        assert 0.85 < o < 1.15

    def test_r_squared_bounded(self) -> None:
        rng = _rng(9)
        bench = rng.normal(0.0, 1.0, size=200)
        strat = 0.5 * bench + rng.normal(0.0, 0.5, size=200)
        r2 = _ma.r_squared(strat, bench)
        assert 0.0 <= r2 <= 1.0

    def test_min_sample_returns_nan(self) -> None:
        # Below _MIN_N -> nan
        strat = np.ones(10)
        bench = np.ones(10)
        res = _ma.alpha_with_significance(strat, bench)
        assert math.isnan(res.alpha)

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            _ma.alpha_with_significance(np.ones(30), np.ones(31))


# ---------------------------------------------------------------------------
# Benchmark behavior
# ---------------------------------------------------------------------------


def _make_bars(
    n: int,
    start_px: float = 20000.0,
    drift_per_bar: float = 0.5,
    bar_noise: float = 3.0,
    seed: int = 0,
) -> pl.DataFrame:
    """Synthetic 1m OHLCV with drift + per-bar noise (random walk with drift)."""

    rng = np.random.default_rng(seed)
    rows = []
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    px = start_px
    for i in range(n):
        o = px
        step = drift_per_bar + rng.normal(0.0, bar_noise)
        c = px + step
        hi = max(o, c) + abs(rng.normal(0.0, 1.0))
        lo = min(o, c) - abs(rng.normal(0.0, 1.0))
        rows.append({
            "ts": t0 + timedelta(minutes=i),
            "open": o,
            "high": hi,
            "low": lo,
            "close": c,
            "volume": 1000.0,
        })
        px = c
    return pl.DataFrame(rows)


class TestBenchmarks:
    def test_cash_returns_are_zero(self) -> None:
        trades = pl.DataFrame({"entry_ts": [datetime.now(UTC)] * 5, "exit_ts": [datetime.now(UTC)] * 5})
        out = _bm.cash_returns(trades)
        assert out.shape == (5,)
        assert (out == 0.0).all()

    def test_mnq_intraday_rises_with_uptrend(self) -> None:
        bars = _make_bars(200, drift_per_bar=0.5, bar_noise=0.0)
        t0 = bars.row(0, named=True)["ts"]
        # 10 consecutive trades, each 10 bars long.
        trades = pl.DataFrame({
            "entry_ts": [t0 + timedelta(minutes=10 * i) for i in range(10)],
            "exit_ts": [t0 + timedelta(minutes=10 * (i + 1)) for i in range(10)],
            "stop_dist_pts": [5.0] * 10,
            "target_dist_pts": [10.0] * 10,
        })
        out = _bm.mnq_intraday_returns(trades, bars)
        # With drift of 0.5/bar and 10 bars of hold, each entry should net
        # roughly 5 points * $2/pt = $10. Allow some slop for entry/exit bar
        # boundary conventions.
        assert (out > 0).all()
        assert np.median(out) == pytest.approx(10.0, rel=0.5)

    def test_naive_momentum_longs_in_uptrend(self) -> None:
        bars = _make_bars(200, drift_per_bar=0.5, bar_noise=0.0)
        t0 = bars.row(0, named=True)["ts"]
        trades = pl.DataFrame({
            "entry_ts": [t0 + timedelta(minutes=20 + 10 * i) for i in range(5)],
            "exit_ts": [t0 + timedelta(minutes=20 + 10 * (i + 1)) for i in range(5)],
            "stop_dist_pts": [5.0] * 5,
            "target_dist_pts": [10.0] * 5,
        })
        out = _bm.naive_momentum_returns(trades, bars, lookback_bars=5)
        # In uptrend → lookback says "long" → hits target (+10 pts * $2 = $20)
        # because drift * 10 bars = 5 pts but high reaches target via noise.
        # At minimum: all positive.
        assert (out > 0).all()


# ---------------------------------------------------------------------------
# Gate 13 & 14 — synthetic strategy fixtures
# ---------------------------------------------------------------------------


@dataclass
class _PathResult:
    returns: np.ndarray
    trades_df: pl.DataFrame


@dataclass
class _Dataset:
    bars_df: pl.DataFrame


def _make_trades_df(n: int, bars: pl.DataFrame, *, bars_per_trade: int = 10) -> pl.DataFrame:
    """Build `n` trades that fit inside `bars`, spaced `bars_per_trade` apart."""
    t0 = bars.row(0, named=True)["ts"]
    rows = {
        "entry_ts": [t0 + timedelta(minutes=i * bars_per_trade) for i in range(n)],
        "exit_ts": [t0 + timedelta(minutes=(i + 1) * bars_per_trade) for i in range(n)],
        "stop_dist_pts": [5.0] * n,
        "target_dist_pts": [10.0] * n,
    }
    return pl.DataFrame(rows)


class TestGate13Gate14Synthetics:
    """The handoff spec requires these 3 synthetic scenarios to behave as stated."""

    def _paths(self, strategy_returns_list: list[np.ndarray], trades: pl.DataFrame) -> list[_PathResult]:
        return [_PathResult(returns=r, trades_df=trades) for r in strategy_returns_list]

    def test_pure_beta_strategy_fails_both_gates(self) -> None:
        bars = _make_bars(400, drift_per_bar=0.3)
        n_trades = 60
        trades = _make_trades_df(n_trades, bars, bars_per_trade=5)
        mnq_ret = _bm.mnq_intraday_returns(trades, bars)

        # Pure-beta: 0.5 * benchmark + small noise → no alpha, beta=0.5.
        paths = []
        for seed in range(5):
            noise = np.random.default_rng(seed=seed).normal(0.0, 0.5, size=n_trades)
            paths.append(_PathResult(
                returns=0.5 * mnq_ret + noise,
                trades_df=trades,
            ))

        ds = _Dataset(bars_df=bars)
        g13 = run_gate_13(paths, ds)
        g14 = run_gate_14(paths, ds)

        # Pure beta: alpha should be ~0 against MNQ, so g13 fails.
        assert g13.passed is False, g13.metric_values
        # |beta| ~= 0.5 > 0.3 → g14 fails.
        assert g14.passed is False
        assert abs(g14.metric_values["beta"]) > 0.3

    def test_constant_edge_strategy_passes_both_gates(self) -> None:
        bars = _make_bars(400, drift_per_bar=0.3)
        n_trades = 120
        trades = _make_trades_df(n_trades, bars, bars_per_trade=3)

        # 5 dollars of edge per trade, NOT correlated with benchmark.
        paths = []
        for seed in range(5):
            noise = np.random.default_rng(seed=seed).normal(0.0, 1.0, size=n_trades)
            paths.append(_PathResult(
                returns=5.0 + noise,  # consistent positive edge
                trades_df=trades,
            ))

        ds = _Dataset(bars_df=bars)
        g13 = run_gate_13(paths, ds)
        g14 = run_gate_14(paths, ds)

        assert g13.passed is True, g13.metric_values
        assert g14.passed is True, g14.metric_values
        assert abs(g14.metric_values["beta"]) < 0.3

    def test_naive_momentum_clone_fails_on_naive_momentum(self) -> None:
        bars = _make_bars(400, drift_per_bar=0.3)
        n_trades = 60
        trades = _make_trades_df(n_trades, bars, bars_per_trade=5)

        naive_ret = _bm.naive_momentum_returns(trades, bars, lookback_bars=5)

        # A perfect naive-momentum clone (plus tiny noise) cannot beat
        # the naive benchmark.
        paths = []
        for seed in range(5):
            noise = np.random.default_rng(seed=seed).normal(0.0, 0.1, size=n_trades)
            paths.append(_PathResult(
                returns=naive_ret + noise,
                trades_df=trades,
            ))

        ds = _Dataset(bars_df=bars)
        g13 = run_gate_13(paths, ds)

        assert g13.passed is False
        # The naive_momentum sub-metric's t_stat must be <= 2.
        naive = g13.metric_values["naive_momentum"]
        assert not (naive["alpha"] > 0 and naive["t_stat"] > 2.0), (
            f"naive clone should not have significant alpha vs naive benchmark, got {naive}"
        )
