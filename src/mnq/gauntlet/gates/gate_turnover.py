"""[REAL] Gate 15: turnover / trades-per-day within bounds.

A scalper with too few trades either has no edge or is over-fitted to a
rare regime — either way its statistical claims are unreliable. A
scalper with too many trades is almost certainly death-by-commissions
and will not survive paper→live.

PASS iff the median across CPCV paths of (trades_per_session_day) falls
within `[min_tpd, max_tpd]`. Default band is [3, 50] — tuned for MNQ
RTH scalping: below 3 is thin, above 50 is (for a human-readable
strategy) suspicious.

Like Gates 13/14, we duck-type on `.trades_df` and expect a column
`entry_ts`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import Any

import numpy as np
import polars as pl

from mnq.gauntlet.gates.gate_attribution import GateResult
from mnq.gauntlet.stats import Bootstrap


@dataclass(frozen=True)
class TurnoverConfig:
    min_trades_per_day: float = 3.0
    max_trades_per_day: float = 50.0
    # Consider a day to be an ET calendar date (RTH boundaries), which for
    # most runs aligns to the dataset's `entry_ts` local date. We keep the
    # tz-handling explicit to avoid silent UTC-vs-ET bugs.
    exchange_tz: str = "America/New_York"
    # Bootstrap confidence interval settings
    use_bootstrap_ci: bool = True
    ci_level: float = 0.95
    n_boot: int = 1000


def _as_trades(obj: Any) -> pl.DataFrame:
    if hasattr(obj, "trades_df"):
        df: pl.DataFrame = obj.trades_df
        return df
    result: pl.DataFrame = obj
    return result


def _trades_per_day(trades: pl.DataFrame, tz: str) -> float:
    if len(trades) == 0:
        return 0.0
    ts = trades["entry_ts"]
    # Convert to naive dates in the target tz.
    dates = ts.dt.replace_time_zone("UTC").dt.convert_time_zone(tz).dt.date().unique()
    n_days = len(dates)
    if n_days == 0:
        return 0.0
    return float(len(trades)) / float(n_days)


def run_gate_15(
    cpcv_results: list[Any],
    *,
    config: TurnoverConfig | None = None,
) -> GateResult:
    cfg = config or TurnoverConfig()
    rates: list[float] = []
    for path in cpcv_results:
        try:
            tpd = _trades_per_day(_as_trades(path), cfg.exchange_tz)
        except Exception as e:  # pragma: no cover — duck-typing fallback
            return GateResult(
                name="gate_15_turnover",
                passed=False,
                metric_values={"error": str(e)},
                failure_reason=f"turnover computation failed: {e}",
            )
        rates.append(tpd)

    median = float(np.median(rates)) if rates else 0.0

    # Prepare metric_values dict
    metric_values: dict[str, Any] = {
        "median_trades_per_day": median,
        "per_path_rates": rates,
        "min_threshold": cfg.min_trades_per_day,
        "max_threshold": cfg.max_trades_per_day,
    }

    # Compute bootstrap CI if requested
    lo: float = median
    hi: float = median
    if cfg.use_bootstrap_ci and rates:
        rates_arr = np.asarray(rates, dtype=np.float64)
        bootstrap = Bootstrap(
            n_boot=cfg.n_boot,
            ci_level=cfg.ci_level,
            seed=42,
        )
        bs_result = bootstrap.estimate(rates_arr, statistic=np.median)
        lo = bs_result.lo
        hi = bs_result.hi
        metric_values["median_trades_per_day_lo"] = lo
        metric_values["median_trades_per_day_hi"] = hi

    # Pass iff the entire CI sits within the band
    passed = cfg.min_trades_per_day <= lo and hi <= cfg.max_trades_per_day

    reason: str | None = None
    if not passed:
        if hi < cfg.min_trades_per_day:
            reason = (
                f"median trades/day CI [{lo:.2f}, {hi:.2f}] < min {cfg.min_trades_per_day:.2f} "
                f"-> strategy too thin; insufficient statistical power"
            )
        elif lo > cfg.max_trades_per_day:
            reason = (
                f"median trades/day CI [{lo:.2f}, {hi:.2f}] > max {cfg.max_trades_per_day:.2f} "
                f"-> strategy likely overtrading; commission-drag risk"
            )
        else:
            reason = (
                f"median trades/day CI [{lo:.2f}, {hi:.2f}] crosses bounds "
                f"[{cfg.min_trades_per_day:.2f}, {cfg.max_trades_per_day:.2f}] "
                f"-> uncertainty is too wide"
            )

    return GateResult(
        name="gate_15_turnover",
        passed=passed,
        metric_values=metric_values,
        failure_reason=reason,
    )


# Re-export for parity with gate_attribution's surface area.
__all__ = ["TurnoverConfig", "run_gate_15"]


# Silence unused-import warnings for the `timezone` symbol — it's there to
# document intent, but polars handles tz conversion natively.
_ = timezone
