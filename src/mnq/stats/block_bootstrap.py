"""[REAL] Block bootstrap confidence interval for per-trade returns.

The 9-gate promotion check (v0.2.4 H4) needs a block-bootstrap CI95
lower bound > +0.05R as gate 3. Block bootstrap (vs. plain bootstrap)
preserves serial correlation -- if your strategy has streaks of wins
followed by streaks of losses, a plain bootstrap underestimates the
true sampling variance.

Algorithm
---------

  1. Given a series of N per-trade returns r_1...r_N
  2. Repeat K times:
     a. Pick ceil(N / block_size) starting indices uniformly with
        replacement from {0, 1, ..., N-1}
     b. For each start, take ``block_size`` consecutive returns
        (wrapping at the end if needed) -- this is a "block"
     c. Concatenate the blocks, truncate to N total returns
     d. Compute the mean of this synthetic series
  3. The K means form an empirical sampling distribution
  4. Return the 2.5% / 97.5% quantiles as the CI95 endpoints

Calibration: block_size=5 trades is a defensible default for
intraday MNQ (most strategies have 1-3 day streaks); k=10000 is
the eta_engine scripts/block_bootstrap.py default. The same
calibration shipped to all the `_promotion_gate.py` evaluator
expects (and the underlying ``eta_engine/scripts/block_bootstrap.py``
uses).

Stdlib-only: no numpy dependency. The runtime imports this for
artifact generation; we don't want to drag numpy into the live
runtime path.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

DEFAULT_BLOCK_SIZE = 5
DEFAULT_K_ITERATIONS = 10_000
DEFAULT_PAPER_GATE_R = 0.05


def _block_resample(
    returns: Sequence[float],
    *,
    block_size: int,
    rng: random.Random,
) -> list[float]:
    """One block-bootstrap resample of ``returns`` with the same length.

    Picks ceil(N/block) starting indices uniformly with replacement,
    extracts blocks (wrapping at the end), concatenates, truncates to N.
    """
    n = len(returns)
    n_blocks = math.ceil(n / block_size)
    out: list[float] = []
    for _ in range(n_blocks):
        start = rng.randint(0, n - 1)
        # Wrap at the end of the series so we don't bias toward
        # short blocks at the tail.
        block = [returns[(start + i) % n] for i in range(block_size)]
        out.extend(block)
    return out[:n]


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (matches numpy's default)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return float(sorted_values[lower])
    frac = pos - lower
    return float(
        sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac,
    )


def block_bootstrap_ci(
    returns: Sequence[float],
    *,
    k: int = DEFAULT_K_ITERATIONS,
    block_size: int = DEFAULT_BLOCK_SIZE,
    seed: int | None = None,
    paper_gate_r: float = DEFAULT_PAPER_GATE_R,
) -> dict[str, Any]:
    """Compute block-bootstrap CI95 of the mean of ``returns``.

    Args:
        returns:        Per-trade R-multiples (positive = win, negative
                        = loss). Empty -> degenerate result.
        k:              Number of bootstrap iterations.
        block_size:     Block size in trades (preserves correlation).
        seed:           Optional RNG seed for reproducibility.
        paper_gate_r:   Reference threshold; the result includes
                        p_above_paper_gate (fraction of bootstrap means
                        > this threshold).

    Returns:
        Dict with:
          n_trades, k, block_size, mean, ci95_low, ci95_high,
          p_above_paper_gate, paper_gate_r

        Empty input -> all stats zero, n_trades=0.
    """
    n = len(returns)
    if n == 0:
        return {
            "n_trades": 0, "k": k, "block_size": block_size,
            "mean": 0.0, "ci95_low": 0.0, "ci95_high": 0.0,
            "p_above_paper_gate": 0.0, "paper_gate_r": paper_gate_r,
        }
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(k):
        sample = _block_resample(returns, block_size=block_size, rng=rng)
        means.append(sum(sample) / n)
    means.sort()
    ci95_low = _quantile(means, 0.025)
    ci95_high = _quantile(means, 0.975)
    n_above = sum(1 for m in means if m > paper_gate_r)
    return {
        "n_trades": n,
        "k": k,
        "block_size": block_size,
        "mean": float(sum(returns) / n),
        "ci95_low": ci95_low,
        "ci95_high": ci95_high,
        "p_above_paper_gate": n_above / k,
        "paper_gate_r": paper_gate_r,
    }
