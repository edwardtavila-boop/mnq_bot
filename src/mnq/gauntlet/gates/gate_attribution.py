"""[REAL] Gates 13 (alpha significance) and 14 (|beta| bounded).

Inputs are a list of CPCV path results and the dataset. We intentionally
accept ducks here: a CPCV path result is anything with
    - `.trades_df  : pl.DataFrame`    per-trade ledger with the columns
                                      benchmarks.py requires
    - `.returns    : np.ndarray`      per-trade returns in USD/contract
A dataset is anything with `.bars_df : pl.DataFrame` providing the 1m
OHLCV frame for the universe of candidate trades.

Decisions matched to the [CONTRACT] spec:
  - gate 13 PASSes iff median alpha > 0 AND median t_stat > 2.0 for every
    benchmark in {cash, mnq_intraday, naive_momentum}.
  - gate 14 PASSes iff |median beta vs mnq_intraday| < 0.3.

See the module-level docstring in the [CONTRACT] version (below) for
failure-message semantics. That wording is preserved verbatim in the
`failure_reason` strings so ops tooling can pattern-match on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from mnq.gauntlet import benchmarks as _bm
from mnq.gauntlet import metrics_attribution as _ma


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    metric_values: dict[str, Any]
    failure_reason: str | None


class CPCVPathResultLike(Protocol):
    returns: Any
    trades_df: Any


class DatasetLike(Protocol):
    bars_df: Any


def _as_returns(obj: Any) -> np.ndarray:
    if hasattr(obj, "returns"):
        return np.asarray(obj.returns, dtype=np.float64)
    return np.asarray(obj, dtype=np.float64)


def _as_trades(obj: Any) -> Any:
    if hasattr(obj, "trades_df"):
        return obj.trades_df
    return obj


def _bench_returns(kind: str, trades: Any, bars: Any) -> np.ndarray:
    if kind == "cash":
        return _bm.cash_returns(trades)
    if kind == "mnq_intraday":
        return _bm.mnq_intraday_returns(trades, bars)
    if kind == "naive_momentum":
        return _bm.naive_momentum_returns(trades, bars)
    raise ValueError(f"unknown benchmark: {kind}")


def _median_nan_safe(vals: list[float]) -> float:
    arr = np.asarray(
        [v for v in vals if not (isinstance(v, float) and np.isnan(v))], dtype=np.float64
    )
    if len(arr) == 0:
        return float("nan")
    return float(np.median(arr))


# ---------------------------------------------------------------------------
# Gate 13 — alpha significance
# ---------------------------------------------------------------------------


_BENCHMARKS = ("cash", "mnq_intraday", "naive_momentum")
_ALPHA_T_THRESHOLD = 2.0


def run_gate_13(cpcv_results: list[Any], dataset: Any) -> GateResult:
    bars = dataset.bars_df if hasattr(dataset, "bars_df") else dataset

    per_bench: dict[str, dict[str, float]] = {}
    for bench_name in _BENCHMARKS:
        alphas: list[float] = []
        ts: list[float] = []
        ps: list[float] = []
        for path in cpcv_results:
            strat = _as_returns(path)
            trades = _as_trades(path)
            bench = _bench_returns(bench_name, trades, bars)
            res = _ma.alpha_with_significance(strat, bench)
            alphas.append(res.alpha)
            ts.append(res.t_stat)
            ps.append(res.p_value)
        per_bench[bench_name] = {
            "alpha": _median_nan_safe(alphas),
            "t_stat": _median_nan_safe(ts),
            "p_value": _median_nan_safe(ps),
        }

    # Aggregate pass/fail per benchmark.
    per_pass = {
        b: (v["alpha"] > 0.0 and v["t_stat"] > _ALPHA_T_THRESHOLD) for b, v in per_bench.items()
    }
    passed = all(per_pass.values())

    reason: str | None = None
    if not passed:
        parts: list[str] = []
        cash_ok = per_pass["cash"]
        mnq_ok = per_pass["mnq_intraday"]
        naive_ok = per_pass["naive_momentum"]
        if cash_ok and not mnq_ok:
            parts.append(
                "alpha against cash significant but not against MNQ-intraday -> likely closet beta"
            )
        if mnq_ok and not naive_ok:
            parts.append(
                "alpha against MNQ-intraday significant but not against "
                "naive-momentum -> likely a more complex momentum strategy "
                "with no real edge over primitive momentum"
            )
        # Underpowered case
        for bench_name2, vals in per_bench.items():
            if vals["alpha"] > 0.0 and vals["t_stat"] <= _ALPHA_T_THRESHOLD:
                parts.append(
                    f"alpha against {bench_name2} positive but t_stat < 2 "
                    f"-> real direction-of-edge but underpowered; suggest more data"
                )
        if not parts:
            failing = [b for b, ok in per_pass.items() if not ok]
            parts.append(f"alpha failed against {failing}")
        reason = "; ".join(parts)

    return GateResult(
        name="gate_13_alpha",
        passed=passed,
        metric_values=per_bench,
        failure_reason=reason,
    )


# ---------------------------------------------------------------------------
# Gate 14 — |beta| against MNQ-intraday
# ---------------------------------------------------------------------------


_BETA_MAX = 0.3


def run_gate_14(cpcv_results: list[Any], dataset: Any) -> GateResult:
    bars = dataset.bars_df if hasattr(dataset, "bars_df") else dataset

    betas: list[float] = []
    for path in cpcv_results:
        strat = _as_returns(path)
        trades = _as_trades(path)
        bench = _bm.mnq_intraday_returns(trades, bars)
        betas.append(_ma.beta(strat, bench))

    med = _median_nan_safe(betas)
    passed = (not np.isnan(med)) and abs(med) < _BETA_MAX

    reason: str | None = None
    if not passed:
        if np.isnan(med):
            reason = "insufficient data to compute beta"
        elif med > 0:
            reason = (
                f"beta = {med:.2f}, mostly long-biased -> strategy mostly takes "
                f"long entries during up moves; consider whether shorts are "
                f"being filtered out by some condition"
            )
        else:
            reason = (
                f"beta = {med:.2f}, mostly short-biased -> strategy mostly "
                f"takes short entries during down moves; mirror of long bias"
            )

    return GateResult(
        name="gate_14_beta",
        passed=passed,
        metric_values={"beta": med, "per_path_betas": betas},
        failure_reason=reason,
    )
