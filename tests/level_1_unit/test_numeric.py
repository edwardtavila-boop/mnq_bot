"""Tests for mnq.core.numeric."""

from __future__ import annotations

import math
from decimal import Decimal

import numpy as np

from mnq.core.numeric import decimal_sum, equity_curve, kahan_sum, max_drawdown


def test_kahan_sum_matches_simple_sum_on_well_conditioned_input():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert kahan_sum(vals) == sum(vals) == 15.0


def test_kahan_sum_matches_fsum_on_pathological_input():
    """Kahan summation should match `math.fsum` on cases where naive `sum`
    loses precision. We do not assert that `sum` is wrong, because CPython's
    `sum` uses branch-free tricks that sometimes happen to produce the exact
    answer — but it's *not guaranteed*. Kahan and fsum are.
    """
    # Alternating huge + tiny values. Ideal result is 1000.0.
    pathological = [1e16] + [1.0] * 1000 + [-1e16]
    kahan = kahan_sum(pathological)
    fsum = math.fsum(pathological)
    assert abs(kahan - 1000.0) < 1e-6
    assert abs(fsum - 1000.0) < 1e-6
    assert kahan == fsum

    # Harder case — interleaved large/small, large/small — that reliably
    # defeats naive left-to-right accumulation.
    interleaved: list[float] = []
    for _ in range(1000):
        interleaved.extend([1e16, 1.0, -1e16, 1.0])
    naive = sum(interleaved)
    kahan2 = kahan_sum(interleaved)
    fsum2 = math.fsum(interleaved)
    # fsum/kahan agree on the true answer (2000.0), naive may or may not.
    assert kahan2 == fsum2 == 2000.0
    # Naive should be off in at least *one* of the two inputs — otherwise the
    # compiler has beaten us and Kahan is pointless. (Sanity check only.)
    assert naive == 2000.0 or naive != 2000.0  # tautology — documentation intent only


def test_equity_curve_starts_at_starting_equity():
    curve = equity_curve([1.0, 2.0, 3.0], starting_equity=100.0)
    assert curve[0] == 100.0
    assert curve[-1] == 106.0
    assert len(curve) == 4


def test_equity_curve_empty_returns_single_element():
    curve = equity_curve([], starting_equity=50.0)
    assert curve.tolist() == [50.0]


def test_decimal_sum_is_exact():
    vals = [Decimal("0.10"), Decimal("0.20"), Decimal("0.30")]
    assert decimal_sum(vals) == Decimal("0.60")


def test_decimal_sum_empty_is_zero():
    assert decimal_sum([]) == Decimal(0)


def test_max_drawdown_zero_on_monotone_up():
    curve = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    dd, pk, tr = max_drawdown(curve)
    assert dd == 0.0


def test_max_drawdown_captures_peak_to_trough():
    # Peak 5 at idx 2, trough 1 at idx 4. Drawdown = 4.
    curve = np.array([3.0, 4.0, 5.0, 3.0, 1.0, 2.0])
    dd, pk, tr = max_drawdown(curve)
    assert dd == 4.0
    assert pk == 2
    assert tr == 4


def test_max_drawdown_empty_array():
    dd, pk, tr = max_drawdown(np.array([]))
    assert (dd, pk, tr) == (0.0, 0, 0)
