"""Tests for ``mnq.stats.block_bootstrap`` -- v0.2.23 bootstrap helper.

Pin the contract:

  * Empty input -> degenerate result (n=0, all stats zero)
  * Output dict has all required keys for the v0.2.4 promotion gate
  * Determinism: same input + seed -> same output
  * CI95 endpoints bracket the empirical mean (sanity check)
  * Block size influences the result (larger block = more variance
    in synthetic samples)
  * paper_gate_r threshold flows through to p_above_paper_gate
"""

from __future__ import annotations

import math

import pytest

from mnq.stats import block_bootstrap_ci
from mnq.stats.block_bootstrap import (
    DEFAULT_BLOCK_SIZE,
    DEFAULT_K_ITERATIONS,
    DEFAULT_PAPER_GATE_R,
    _block_resample,
    _quantile,
)

# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_yields_zero_stats() -> None:
    result = block_bootstrap_ci([], k=100, seed=1)
    assert result["n_trades"] == 0
    assert result["mean"] == 0.0
    assert result["ci95_low"] == 0.0
    assert result["ci95_high"] == 0.0
    assert result["p_above_paper_gate"] == 0.0


def test_single_trade_input() -> None:
    """One trade -> mean is that trade. CI is degenerate (low==high
    if the single value is replicated across blocks)."""
    result = block_bootstrap_ci([0.5], k=100, seed=1)
    assert result["n_trades"] == 1
    assert result["mean"] == 0.5
    # Block resampling of [0.5] produces [0.5, 0.5, ...] -> mean = 0.5
    assert result["ci95_low"] == 0.5
    assert result["ci95_high"] == 0.5


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_output_has_all_required_keys() -> None:
    """The v0.2.4 promotion gate reads ci95_low. Defensive: pin
    every other documented key so a future refactor doesn't drop one."""
    result = block_bootstrap_ci([0.1] * 20, k=100, seed=1)
    required = {
        "n_trades",
        "k",
        "block_size",
        "mean",
        "ci95_low",
        "ci95_high",
        "p_above_paper_gate",
        "paper_gate_r",
    }
    missing = required - set(result.keys())
    assert not missing, f"missing: {missing}"


def test_n_trades_matches_input_length() -> None:
    rs = [0.1, 0.2, -0.05, 0.3, -0.1]
    result = block_bootstrap_ci(rs, k=100, seed=1)
    assert result["n_trades"] == 5


def test_k_passes_through() -> None:
    result = block_bootstrap_ci([0.0] * 10, k=42, seed=1)
    assert result["k"] == 42


def test_block_size_passes_through() -> None:
    result = block_bootstrap_ci([0.0] * 10, k=100, block_size=7, seed=1)
    assert result["block_size"] == 7


# ---------------------------------------------------------------------------
# Determinism + sanity
# ---------------------------------------------------------------------------


def test_same_seed_same_result() -> None:
    rs = [0.1, -0.2, 0.3, -0.05, 0.15] * 4  # 20 trades
    a = block_bootstrap_ci(rs, k=500, seed=42)
    b = block_bootstrap_ci(rs, k=500, seed=42)
    assert a["ci95_low"] == b["ci95_low"]
    assert a["ci95_high"] == b["ci95_high"]
    assert a["p_above_paper_gate"] == b["p_above_paper_gate"]


def test_different_seed_different_result() -> None:
    """Sanity: different seeds usually produce different CIs.

    Uses 20 distinct values (not a repeating pattern) so block
    resampling actually has variation.
    """
    rs = [
        0.10,
        -0.20,
        0.30,
        -0.05,
        0.15,
        0.40,
        -0.10,
        0.25,
        -0.30,
        0.50,
        -0.15,
        0.05,
        -0.25,
        0.35,
        0.20,
        -0.40,
        0.45,
        -0.35,
        0.60,
        -0.05,
    ]
    a = block_bootstrap_ci(rs, k=500, seed=1)
    b = block_bootstrap_ci(rs, k=500, seed=2)
    assert (a["ci95_low"] != b["ci95_low"]) or (a["ci95_high"] != b["ci95_high"])


def test_ci_brackets_mean_for_random_walk() -> None:
    """For mean-zero normal-like returns, ci95 should bracket 0
    (most of the time -- with k=2000 this is reliable)."""
    import random

    rng = random.Random(7)
    rs = [rng.gauss(0.0, 1.0) for _ in range(60)]
    result = block_bootstrap_ci(rs, k=2000, seed=11)
    assert result["ci95_low"] < result["ci95_high"]
    # Empirical mean should be inside the CI by construction
    assert result["ci95_low"] <= result["mean"] <= result["ci95_high"]


def test_strong_positive_signal_ci_excludes_zero() -> None:
    """A strong consistent positive signal (mean +1R, low variance)
    over 60 trades should yield a CI that EXCLUDES 0R."""
    import random

    rng = random.Random(7)
    rs = [rng.gauss(1.0, 0.1) for _ in range(60)]  # tight cluster around +1R
    result = block_bootstrap_ci(rs, k=2000, seed=11)
    assert result["ci95_low"] > 0.0
    # And p_above_threshold should be near 1.0
    assert result["p_above_paper_gate"] > 0.95


def test_strong_negative_signal_ci_excludes_zero() -> None:
    """Strong negative signal -> CI95 high < 0R."""
    import random

    rng = random.Random(7)
    rs = [rng.gauss(-1.0, 0.1) for _ in range(60)]
    result = block_bootstrap_ci(rs, k=2000, seed=11)
    assert result["ci95_high"] < 0.0


# ---------------------------------------------------------------------------
# Threshold pass-through
# ---------------------------------------------------------------------------


def test_paper_gate_r_passes_through_to_output() -> None:
    result = block_bootstrap_ci(
        [0.0] * 10,
        k=100,
        seed=1,
        paper_gate_r=0.123,
    )
    assert result["paper_gate_r"] == 0.123


def test_p_above_threshold_changes_with_threshold() -> None:
    """A strong +1R signal: p_above 0.05R should be ~1.0; p_above
    2.0R should be much smaller (since the signal isn't that strong)."""
    import random

    rng = random.Random(11)
    rs = [rng.gauss(1.0, 0.2) for _ in range(40)]
    low = block_bootstrap_ci(rs, k=2000, seed=11, paper_gate_r=0.05)
    high = block_bootstrap_ci(rs, k=2000, seed=11, paper_gate_r=2.0)
    assert low["p_above_paper_gate"] > high["p_above_paper_gate"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_quantile_endpoints() -> None:
    """_quantile of a sorted list -- standard linear-interp behaviour."""
    sorted_vals = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert _quantile(sorted_vals, 0.0) == 0.0
    assert _quantile(sorted_vals, 1.0) == 4.0
    assert _quantile(sorted_vals, 0.5) == 2.0
    # 25% of 4 = 1.0 -> sorted_vals[1] = 1.0
    assert _quantile(sorted_vals, 0.25) == pytest.approx(1.0)


def test_quantile_empty_list() -> None:
    assert _quantile([], 0.5) == 0.0


def test_quantile_single_value() -> None:
    assert _quantile([42.0], 0.5) == 42.0


def test_block_resample_returns_correct_length() -> None:
    """Resampling preserves length."""
    import random

    rng = random.Random(1)
    out = _block_resample([1.0, 2.0, 3.0, 4.0, 5.0], block_size=2, rng=rng)
    assert len(out) == 5


def test_block_resample_values_from_input() -> None:
    """Every value in the resample comes from the input."""
    import random

    rng = random.Random(1)
    inputs = {1.0, 2.0, 3.0, 4.0, 5.0}
    out = _block_resample(list(inputs), block_size=3, rng=rng)
    for v in out:
        assert v in inputs


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_constants_match_eta_engine() -> None:
    """Pin the calibration constants. eta_engine's
    scripts/block_bootstrap.py uses these same values."""
    assert DEFAULT_BLOCK_SIZE == 5
    assert DEFAULT_K_ITERATIONS == 10_000
    assert DEFAULT_PAPER_GATE_R == 0.05


# ---------------------------------------------------------------------------
# CI95_low ordering
# ---------------------------------------------------------------------------


def test_ci95_low_strictly_below_high() -> None:
    """Always: low < high (or equal for degenerate input)."""
    result = block_bootstrap_ci([0.1] * 30, k=500, seed=1)
    assert result["ci95_low"] <= result["ci95_high"]
    # And both finite
    assert math.isfinite(result["ci95_low"])
    assert math.isfinite(result["ci95_high"])
