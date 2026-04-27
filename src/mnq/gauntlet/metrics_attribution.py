"""[REAL] Performance attribution metrics: alpha, beta, IR, R², and friends.

Implemented with numpy only (no statsmodels dependency). The HAC estimator
reproduces the Newey-West (1987) formula with the Bartlett kernel and the
Newey-West (1994) data-driven bandwidth L = floor(4 * (N/100)^(2/9)).

All inputs are float64 arrays of per-trade returns in USD per contract,
aligned 1:1. See the original [CONTRACT] docstring for the full API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl
from scipy import stats as _sp_stats


@dataclass(frozen=True)
class AlphaResult:
    alpha: float
    alpha_se: float
    t_stat: float
    p_value: float
    n: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_MIN_N = 30
_NAN_RESULT = AlphaResult(
    alpha=float("nan"),
    alpha_se=float("nan"),
    t_stat=float("nan"),
    p_value=float("nan"),
    n=0,
)


def _check_same_len(a: np.ndarray, b: np.ndarray) -> None:
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")


def _as_float(a: np.ndarray | list[float] | tuple[float, ...]) -> np.ndarray:
    arr = np.asarray(a, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError(f"expected 1-D array, got shape {arr.shape}")
    return arr


def _nw_bandwidth(n: int) -> int:
    """Newey-West (1994) plug-in bandwidth."""
    return max(1, int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))))


def _bartlett_hac_cov(x: np.ndarray, residuals: np.ndarray, max_lag: int) -> np.ndarray:
    """HAC covariance for OLS coefficients.

    Returns a (k, k) matrix. Expects `x` shaped (N, k) including intercept
    column if applicable, and residuals shape (N,).
    """
    n = x.shape[0]
    # Score vectors s_t = residuals_t * x_t (shape (N, k))
    s = residuals[:, None] * x
    # Autocovariance terms: Gamma_l = (1/N) * sum_{t=l+1..N} s_t s_{t-l}^T
    gamma0 = (s.T @ s) / n
    omega = gamma0.copy()
    for lag in range(1, max_lag + 1):
        s_t = s[lag:]
        s_lag = s[:-lag]
        gamma_l = (s_t.T @ s_lag) / n
        w = 1.0 - lag / (max_lag + 1.0)  # Bartlett kernel
        omega += w * (gamma_l + gamma_l.T)

    # (X'X / N)^-1 * Omega * (X'X / N)^-1 / N
    xtx_inv_n = np.linalg.inv(x.T @ x / n)
    cov = xtx_inv_n @ omega @ xtx_inv_n / n
    return np.asarray(cov, dtype=np.float64)


def _one_sample_alpha(strategy: np.ndarray) -> AlphaResult:
    """Alpha against zeros: just a t-test of mean(strategy) vs 0.

    Uses a HAC-robust SE (bandwidth via NW plug-in). For uncorrelated
    returns this collapses to the usual s / sqrt(n).
    """
    n = len(strategy)
    mean = float(strategy.mean())
    resid = strategy - mean
    nw_lag = _nw_bandwidth(n)
    # Reduce HAC to the 1-D intercept-only case.
    x = np.ones((n, 1), dtype=np.float64)
    cov = _bartlett_hac_cov(x, resid, nw_lag)
    se = float(math.sqrt(max(cov[0, 0], 0.0)))
    if se == 0.0:
        t = float("inf") if mean != 0 else 0.0
        p = 0.0 if mean != 0 else 1.0
    else:
        t = mean / se
        p = 2.0 * (1.0 - _sp_stats.t.cdf(abs(t), df=max(n - 1, 1)))
    return AlphaResult(alpha=mean, alpha_se=se, t_stat=float(t), p_value=float(p), n=n)


# ---------------------------------------------------------------------------
# Gate metrics
# ---------------------------------------------------------------------------


def alpha_with_significance(
    strategy_returns: np.ndarray,
    benchmark_returns: np.ndarray,
) -> AlphaResult:
    strat = _as_float(strategy_returns)
    bench = _as_float(benchmark_returns)
    _check_same_len(strat, bench)
    n = len(strat)
    if n < _MIN_N:
        return AlphaResult(
            alpha=float("nan"),
            alpha_se=float("nan"),
            t_stat=float("nan"),
            p_value=float("nan"),
            n=n,
        )

    # If strategy is constant (zero variance), alpha degenerates.
    if float(strat.std(ddof=0)) < 1e-12:
        return AlphaResult(alpha=0.0, alpha_se=0.0, t_stat=0.0, p_value=1.0, n=n)

    # If benchmark is constant (e.g., cash = zeros), degenerate to one-sample.
    if float(bench.std(ddof=0)) < 1e-12:
        return _one_sample_alpha(strat)

    # OLS: strat = a + b * bench + eps
    x = np.column_stack([np.ones(n), bench])  # (N, 2)
    beta, *_ = np.linalg.lstsq(x, strat, rcond=None)
    a, b = float(beta[0]), float(beta[1])
    resid = strat - (a + b * bench)

    nw_lag = _nw_bandwidth(n)
    cov = _bartlett_hac_cov(x, resid, nw_lag)
    var_alpha = float(cov[0, 0])
    se = math.sqrt(max(var_alpha, 0.0))

    if se == 0.0:
        t = 0.0
        p = 1.0
    else:
        t = a / se
        p = 2.0 * (1.0 - _sp_stats.t.cdf(abs(t), df=max(n - 2, 1)))

    return AlphaResult(alpha=a, alpha_se=se, t_stat=float(t), p_value=float(p), n=n)


def beta(strategy_returns: np.ndarray, benchmark_returns: np.ndarray) -> float:
    strat = _as_float(strategy_returns)
    bench = _as_float(benchmark_returns)
    _check_same_len(strat, bench)
    n = len(strat)
    if n < _MIN_N:
        return float("nan")
    if float(bench.std(ddof=0)) < 1e-12:
        return 0.0
    if float(strat.std(ddof=0)) < 1e-12:
        return 0.0
    covmat = np.cov(strat, bench, ddof=1)
    var_b = float(covmat[1, 1])
    if var_b <= 1e-12:
        return 0.0
    return float(covmat[0, 1] / var_b)


def information_ratio(
    strategy_returns: np.ndarray,
    benchmark_returns: np.ndarray,
    trades_per_year: float,
) -> float:
    res = alpha_with_significance(strategy_returns, benchmark_returns)
    if math.isnan(res.alpha) or res.alpha_se == 0.0 or math.isnan(res.alpha_se):
        return float("nan")
    ann = math.sqrt(max(trades_per_year, 0.0))
    return float(ann * (res.alpha / res.alpha_se))


def r_squared(strategy_returns: np.ndarray, benchmark_returns: np.ndarray) -> float:
    strat = _as_float(strategy_returns)
    bench = _as_float(benchmark_returns)
    _check_same_len(strat, bench)
    n = len(strat)
    if n < _MIN_N:
        return float("nan")
    if float(bench.std(ddof=0)) < 1e-12:
        return 0.0
    if float(strat.std(ddof=0)) < 1e-12:
        return 0.0
    x = np.column_stack([np.ones(n), bench])
    coef, *_ = np.linalg.lstsq(x, strat, rcond=None)
    pred = x @ coef
    ss_res = float(((strat - pred) ** 2).sum())
    ss_tot = float(((strat - strat.mean()) ** 2).sum())
    if ss_tot <= 1e-12:
        return 0.0
    return float(1.0 - ss_res / ss_tot)


def treynor_ratio(
    strategy_returns: np.ndarray,
    benchmark_returns: np.ndarray,
    rf: float = 0.0,
) -> float:
    b = beta(strategy_returns, benchmark_returns)
    if math.isnan(b) or abs(b) < 0.05:
        return float("nan")
    strat = _as_float(strategy_returns)
    return float((strat.mean() - rf) / b)


def sortino(strategy_returns: np.ndarray, mar: float = 0.0) -> float:
    strat = _as_float(strategy_returns)
    if len(strat) < _MIN_N:
        return float("nan")
    excess = strat - mar
    downside = np.minimum(0.0, excess)
    denom = math.sqrt(float((downside**2).mean()))
    if denom <= 1e-12:
        return float("nan") if float(excess.mean()) <= 0.0 else float("inf")
    return float(excess.mean() / denom)


def calmar(strategy_returns: np.ndarray, equity_curve: np.ndarray) -> float:
    strat = _as_float(strategy_returns)
    eq = _as_float(equity_curve)
    if len(strat) < _MIN_N or len(eq) < 2:
        return float("nan")
    # max drawdown as fraction of peak equity (with +1 base offset so a
    # zero-start equity curve doesn't division-by-zero).
    running_max = np.maximum.accumulate(eq)
    drawdown = (eq - running_max) / (np.abs(running_max) + 1.0)
    max_dd = float(abs(drawdown.min()))
    if max_dd <= 1e-12:
        return float("inf") if float(strat.mean()) > 0.0 else float("nan")
    return float(strat.mean() / max_dd)


def omega(strategy_returns: np.ndarray, threshold: float = 0.0) -> float:
    strat = _as_float(strategy_returns)
    if len(strat) < _MIN_N:
        return float("nan")
    gains = np.maximum(0.0, strat - threshold).sum()
    losses = np.maximum(0.0, threshold - strat).sum()
    if losses <= 1e-12:
        return float("inf") if gains > 0 else float("nan")
    return float(gains / losses)


def _kappa_n(strategy: np.ndarray, mar: float, n_order: int) -> float:
    excess = strategy - mar
    lpm = float((np.maximum(0.0, mar - strategy) ** n_order).mean())
    if lpm <= 1e-12:
        return float("nan") if float(excess.mean()) <= 0.0 else float("inf")
    return float(excess.mean() / (lpm ** (1.0 / n_order)))


def kappa3(strategy_returns: np.ndarray, mar: float = 0.0) -> float:
    strat = _as_float(strategy_returns)
    if len(strat) < _MIN_N:
        return float("nan")
    return _kappa_n(strat, mar, 3)


def kappa4(strategy_returns: np.ndarray, mar: float = 0.0) -> float:
    strat = _as_float(strategy_returns)
    if len(strat) < _MIN_N:
        return float("nan")
    return _kappa_n(strat, mar, 4)


def downside_capture(strategy_returns: np.ndarray, benchmark_returns: np.ndarray) -> float:
    strat = _as_float(strategy_returns)
    bench = _as_float(benchmark_returns)
    _check_same_len(strat, bench)
    mask = bench < 0
    denom = float(bench[mask].sum())
    if abs(denom) <= 1e-12:
        return float("nan")
    return float(strat[mask].sum() / denom)


def upside_capture(strategy_returns: np.ndarray, benchmark_returns: np.ndarray) -> float:
    strat = _as_float(strategy_returns)
    bench = _as_float(benchmark_returns)
    _check_same_len(strat, bench)
    mask = bench > 0
    denom = float(bench[mask].sum())
    if abs(denom) <= 1e-12:
        return float("nan")
    return float(strat[mask].sum() / denom)


def rolling_alpha_beta(
    strategy_returns: np.ndarray,
    benchmark_returns: np.ndarray,
    window: int = 30,
) -> pl.DataFrame:
    strat = _as_float(strategy_returns)
    bench = _as_float(benchmark_returns)
    _check_same_len(strat, bench)
    n = len(strat)
    rows = []
    for end in range(window, n + 1):
        s = strat[end - window : end]
        b = bench[end - window : end]
        res = alpha_with_significance(s, b) if window >= _MIN_N else _NAN_RESULT
        rows.append(
            {
                "trade_index_end": end - 1,
                "alpha": res.alpha,
                "alpha_se": res.alpha_se,
                "beta": beta(s, b) if window >= _MIN_N else float("nan"),
            }
        )
    return pl.DataFrame(rows)
