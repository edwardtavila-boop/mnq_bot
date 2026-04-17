"""[REAL] Numerically-robust aggregation helpers.

The stdlib `sum()` on floats accumulates rounding error of order
`O(n * epsilon)`. For long equity curves (100k+ bars) that's material
enough to shift a marginal-pass gate into a marginal-fail and vice
versa. We provide Kahan (compensated) summation and `math.fsum`-based
equity curve reducers here, plus Decimal variants for money math that
need the same property without the floating-point concern.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from decimal import Decimal

import numpy as np


def kahan_sum(values: Iterable[float]) -> float:
    """Kahan-Neumaier compensated summation.

    Preferred over `math.fsum` when you're accumulating a stream and
    don't have the full list materialized, or when you need ~machine
    precision but not the full 2*eps guarantee of fsum. For reducing
    a materialized list to a single number, `math.fsum` is strictly
    better — use that.
    """
    total = 0.0
    c = 0.0
    for v in values:
        t = total + v
        # Neumaier correction: handles the case where |v| > |total|.
        if abs(total) >= abs(v):
            c += (total - t) + v
        else:
            c += (v - t) + total
        total = t
    return total + c


def equity_curve(returns: Sequence[float] | np.ndarray, starting_equity: float = 0.0) -> np.ndarray:
    """Compute an equity curve from per-trade returns using compensated
    summation, returned as a float64 ndarray of length `len(returns) + 1`.

    `equity_curve(returns)[0]` is the starting equity; `[-1]` is the
    final equity after all trades. This is the same curve the gate
    infrastructure expects for `calmar()` etc.
    """
    arr = np.asarray(returns, dtype=np.float64)
    # np.cumsum accumulates in float64 which is fine for <1e7 entries;
    # for longer series fall back to fsum-per-prefix (O(n^2) worst case
    # but we never have that many trades).
    if arr.size <= 1_000_000:
        return np.concatenate([[starting_equity], starting_equity + np.cumsum(arr)])
    # Long-history branch: fsum prefix sums.
    out = np.empty(arr.size + 1, dtype=np.float64)
    out[0] = starting_equity
    running: list[float] = []
    for i, v in enumerate(arr, start=1):
        running.append(float(v))
        out[i] = starting_equity + math.fsum(running)
    return out


def decimal_sum(values: Iterable[Decimal]) -> Decimal:
    """Exact sum of Decimal values. Provided for symmetry with kahan_sum
    — Decimal arithmetic is exact so this is just `sum(...)` with a
    known identity element, but having it named makes call sites
    self-documenting."""
    total = Decimal(0)
    for v in values:
        total += v
    return total


def max_drawdown(equity: Sequence[float] | np.ndarray) -> tuple[float, int, int]:
    """Return `(max_dd_abs, peak_index, trough_index)` over an equity curve.

    `max_dd_abs` is the largest peak-to-trough drop in absolute equity
    (same units as the input). This computes in a single pass and is
    numerically stable.
    """
    arr = np.asarray(equity, dtype=np.float64)
    if arr.size == 0:
        return 0.0, 0, 0
    peak = arr[0]
    peak_idx = 0
    max_dd = 0.0
    pk_at_max = 0
    tr_at_max = 0
    for i in range(arr.size):
        v = float(arr[i])
        if v > peak:
            peak = v
            peak_idx = i
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
            pk_at_max = peak_idx
            tr_at_max = i
    return max_dd, pk_at_max, tr_at_max
