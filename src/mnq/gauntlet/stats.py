"""Non-parametric bootstrap estimator for gate metrics and A/B testing.

Provides deterministic (seeded) bootstrap confidence intervals using the
percentile method. Used to quantify uncertainty in gate metrics like
median trades-per-day, and to compare baseline vs candidate strategies
in A/B tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BootstrapResult:
    """Result of a bootstrap estimation: point estimate and percentile CI."""

    point: float
    lo: float
    hi: float
    n: int
    ci_level: float
    n_boot: int

    @property
    def width(self) -> float:
        """Width of the confidence interval."""
        return self.hi - self.lo


@dataclass(frozen=True)
class Bootstrap:
    """Non-parametric bootstrap estimator.

    Provides (estimate, [lo, hi]) for a point statistic computed from a
    1-D sample. Resample with replacement n_boot times; percentile CI.
    Deterministic via seeded numpy Generator.
    """

    n_boot: int = 1000
    ci_level: float = 0.95
    seed: int = 42

    def estimate(
        self,
        sample: np.ndarray,
        statistic: Callable[[np.ndarray], float] = np.mean,
    ) -> BootstrapResult:
        """Compute bootstrap estimate + percentile CI.

        Args:
            sample: 1-D array of observations.
            statistic: Function that computes the point statistic
                      (default: np.mean). Can be any callable that takes
                      a 1-D array and returns a float.

        Returns:
            BootstrapResult with point estimate, CI bounds, and metadata.

        Raises:
            ValueError: If sample is empty or not a 1-D array.
        """
        sample_arr = np.asarray(sample, dtype=np.float64)

        if sample_arr.size == 0:
            raise ValueError("cannot bootstrap from empty sample")
        if sample_arr.ndim != 1:
            raise ValueError("sample must be 1-dimensional")

        n = len(sample_arr)

        # Compute point estimate from the original sample
        point = float(statistic(sample_arr))

        # Create a seeded generator for reproducibility
        rng = np.random.Generator(np.random.PCG64(self.seed))

        # Resample with replacement n_boot times
        bootstrap_estimates: list[float] = []
        for _ in range(self.n_boot):
            # Resample indices with replacement
            indices = rng.choice(n, size=n, replace=True)
            resampled = sample_arr[indices]
            # Compute statistic on the resampled data
            bootstrap_estimates.append(float(statistic(resampled)))

        # Compute percentile CI
        alpha = 1.0 - self.ci_level
        lo_pct = 100.0 * (alpha / 2.0)
        hi_pct = 100.0 * (1.0 - alpha / 2.0)

        lo = float(np.percentile(bootstrap_estimates, lo_pct))
        hi = float(np.percentile(bootstrap_estimates, hi_pct))

        return BootstrapResult(
            point=point,
            lo=lo,
            hi=hi,
            n=n,
            ci_level=self.ci_level,
            n_boot=self.n_boot,
        )


def paired_bootstrap(
    a: np.ndarray,
    b: np.ndarray,
    *,
    statistic: Callable[[np.ndarray, np.ndarray], float],
    n_boot: int = 1000,
    ci_level: float = 0.95,
    seed: int = 42,
) -> BootstrapResult:
    """Paired bootstrap for A/B comparison (e.g. baseline vs mutation).

    Resamples pairs (a[i], b[i]) with replacement and computes the
    difference statistic for each resample. Returns a CI for the
    difference.

    Args:
        a: Baseline sample (1-D array).
        b: Candidate sample (1-D array).
        statistic: Function(a, b) -> float that computes the difference
                  metric (e.g., lambda x, y: np.mean(y) - np.mean(x)).
        n_boot: Number of bootstrap resamples (default: 1000).
        ci_level: Confidence level for the CI (default: 0.95).
        seed: Seed for reproducibility (default: 42).

    Returns:
        BootstrapResult with point estimate of the difference and CI.

    Raises:
        ValueError: If arrays are empty or have mismatched lengths.
    """
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)

    if a_arr.size == 0 or b_arr.size == 0:
        raise ValueError("cannot bootstrap from empty samples")
    if len(a_arr) != len(b_arr):
        raise ValueError("a and b must have the same length")
    if a_arr.ndim != 1 or b_arr.ndim != 1:
        raise ValueError("samples must be 1-dimensional")

    n = len(a_arr)

    # Compute point estimate of the difference
    point = float(statistic(a_arr, b_arr))

    # Create a seeded generator for reproducibility
    rng = np.random.Generator(np.random.PCG64(seed))

    # Resample with replacement n_boot times
    bootstrap_estimates: list[float] = []
    for _ in range(n_boot):
        # Resample indices with replacement
        indices = rng.choice(n, size=n, replace=True)
        resampled_a = a_arr[indices]
        resampled_b = b_arr[indices]
        # Compute statistic on the resampled pair
        bootstrap_estimates.append(float(statistic(resampled_a, resampled_b)))

    # Compute percentile CI
    alpha = 1.0 - ci_level
    lo_pct = 100.0 * (alpha / 2.0)
    hi_pct = 100.0 * (1.0 - alpha / 2.0)

    lo = float(np.percentile(bootstrap_estimates, lo_pct))
    hi = float(np.percentile(bootstrap_estimates, hi_pct))

    return BootstrapResult(
        point=point,
        lo=lo,
        hi=hi,
        n=n,
        ci_level=ci_level,
        n_boot=n_boot,
    )


def minimum_effect_size(
    baseline_ci: BootstrapResult,
    candidate_ci: BootstrapResult,
    direction: str = "greater",
) -> bool:
    """Check if candidate is reliably better than baseline.

    Returns True iff the candidate CI and baseline CI don't overlap
    in the asserted direction.

    Args:
        baseline_ci: Bootstrap CI for baseline strategy.
        candidate_ci: Bootstrap CI for candidate strategy.
        direction: "greater" (candidate > baseline) or "less"
                  (candidate < baseline).

    Returns:
        True if CIs don't overlap in the stated direction, False otherwise.
    """
    if direction == "greater":
        # candidate is reliably greater iff its lower bound > baseline's upper bound
        return candidate_ci.lo > baseline_ci.hi
    if direction == "less":
        # candidate is reliably less iff its upper bound < baseline's lower bound
        return candidate_ci.hi < baseline_ci.lo
    raise ValueError(f"unknown direction: {direction}")


__all__ = [
    "Bootstrap",
    "BootstrapResult",
    "paired_bootstrap",
    "minimum_effect_size",
]
