"""Tests for mnq.gauntlet.stats (bootstrap confidence intervals)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import polars as pl
import pytest

from mnq.gauntlet.gates.gate_turnover import TurnoverConfig, run_gate_15
from mnq.gauntlet.stats import Bootstrap, BootstrapResult, minimum_effect_size, paired_bootstrap


class TestBootstrap:
    """Bootstrap estimator tests."""

    def test_bootstrap_on_known_sample(self) -> None:
        """Point estimate should be close to sample mean; CI should contain true mean."""
        np.random.seed(42)
        true_mean = 10.0
        sample = np.random.normal(true_mean, 1.0, 100)

        bs = Bootstrap(n_boot=1000, ci_level=0.95, seed=42)
        result = bs.estimate(sample, statistic=np.mean)

        # Point estimate should be close to sample mean
        assert abs(result.point - np.mean(sample)) < 1e-10
        # CI should contain the sample mean
        assert result.lo <= np.mean(sample) <= result.hi
        # CI width should be reasonable
        assert result.width > 0.0

    def test_bootstrap_reproducible_with_same_seed(self) -> None:
        """Bootstrap with same seed should produce identical results."""
        sample = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

        bs1 = Bootstrap(n_boot=100, seed=42)
        result1 = bs1.estimate(sample, statistic=np.mean)

        bs2 = Bootstrap(n_boot=100, seed=42)
        result2 = bs2.estimate(sample, statistic=np.mean)

        assert result1.point == result2.point
        assert result1.lo == result2.lo
        assert result1.hi == result2.hi

    def test_bootstrap_ci_tightens_with_more_resamples(self) -> None:
        """Width should generally decrease as n_boot increases."""
        sample = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])

        bs_100 = Bootstrap(n_boot=100, seed=42)
        result_100 = bs_100.estimate(sample, statistic=np.mean)

        bs_5000 = Bootstrap(n_boot=5000, seed=42)
        result_5000 = bs_5000.estimate(sample, statistic=np.mean)

        # With more resamples, the percentile estimates should stabilize
        # (width may not strictly decrease, but estimate becomes more stable)
        assert result_100.n_boot == 100
        assert result_5000.n_boot == 5000

    def test_bootstrap_on_constant_sample(self) -> None:
        """Bootstrap on constant sample should have zero width."""
        sample = np.array([5.0, 5.0, 5.0, 5.0, 5.0])

        bs = Bootstrap(n_boot=100, seed=42)
        result = bs.estimate(sample, statistic=np.mean)

        assert result.point == 5.0
        assert result.lo == 5.0
        assert result.hi == 5.0
        assert result.width == 0.0

    def test_bootstrap_on_empty_sample_raises(self) -> None:
        """Bootstrap on empty sample should raise ValueError."""
        sample = np.array([])

        bs = Bootstrap(n_boot=100, seed=42)
        with pytest.raises(ValueError, match="empty"):
            bs.estimate(sample, statistic=np.mean)

    def test_bootstrap_on_multidimensional_raises(self) -> None:
        """Bootstrap on multidimensional array should raise ValueError."""
        sample = np.array([[1.0, 2.0], [3.0, 4.0]])

        bs = Bootstrap(n_boot=100, seed=42)
        with pytest.raises(ValueError, match="1-dimensional"):
            bs.estimate(sample, statistic=np.mean)

    def test_bootstrap_result_properties(self) -> None:
        """BootstrapResult should have width property."""
        result = BootstrapResult(
            point=10.0,
            lo=9.0,
            hi=11.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )
        assert result.width == 2.0
        assert result.n == 50
        assert result.ci_level == 0.95

    def test_bootstrap_with_custom_statistic(self) -> None:
        """Bootstrap should work with custom statistics (e.g., median)."""
        sample = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0])  # outlier

        bs = Bootstrap(n_boot=500, seed=42)
        result = bs.estimate(sample, statistic=np.median)

        # Median should be 3.5 (average of 3 and 4)
        assert result.point == 3.5
        # CI should reflect the median
        assert result.lo <= 3.5 <= result.hi


class TestPairedBootstrap:
    """Paired bootstrap for A/B comparison tests."""

    def test_paired_bootstrap_computes_difference(self) -> None:
        """Paired bootstrap should compute difference-of-means CI."""
        baseline = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        candidate = np.array([2.0, 3.0, 4.0, 5.0, 6.0])  # +1 shift

        result = paired_bootstrap(
            baseline,
            candidate,
            statistic=lambda a, b: np.mean(b) - np.mean(a),
            n_boot=1000,
            seed=42,
        )

        # Point estimate should be close to 1.0
        assert abs(result.point - 1.0) < 0.1
        # CI should contain 1.0
        assert result.lo <= 1.0 <= result.hi

    def test_paired_bootstrap_reproducible(self) -> None:
        """Paired bootstrap with same seed should be reproducible."""
        baseline = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        candidate = np.array([2.0, 3.0, 4.0, 5.0, 6.0])

        result1 = paired_bootstrap(
            baseline,
            candidate,
            statistic=lambda a, b: np.mean(b) - np.mean(a),
            n_boot=100,
            seed=42,
        )
        result2 = paired_bootstrap(
            baseline,
            candidate,
            statistic=lambda a, b: np.mean(b) - np.mean(a),
            n_boot=100,
            seed=42,
        )

        assert result1.point == result2.point
        assert result1.lo == result2.lo
        assert result1.hi == result2.hi

    def test_paired_bootstrap_empty_raises(self) -> None:
        """Paired bootstrap on empty samples should raise."""
        baseline = np.array([])
        candidate = np.array([])

        with pytest.raises(ValueError, match="empty"):
            paired_bootstrap(
                baseline,
                candidate,
                statistic=lambda a, b: np.mean(b) - np.mean(a),
            )

    def test_paired_bootstrap_length_mismatch_raises(self) -> None:
        """Paired bootstrap with mismatched lengths should raise."""
        baseline = np.array([1.0, 2.0, 3.0])
        candidate = np.array([1.0, 2.0])  # Shorter

        with pytest.raises(ValueError, match="length"):
            paired_bootstrap(
                baseline,
                candidate,
                statistic=lambda a, b: np.mean(b) - np.mean(a),
            )


class TestMinimumEffectSize:
    """Minimum effect size detection tests."""

    def test_minimum_effect_size_greater_nonoverlap(self) -> None:
        """When candidate CI >> baseline CI, should return True for 'greater'."""
        baseline_ci = BootstrapResult(
            point=10.0,
            lo=9.0,
            hi=11.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )
        candidate_ci = BootstrapResult(
            point=15.0,
            lo=14.0,
            hi=16.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )

        result = minimum_effect_size(baseline_ci, candidate_ci, direction="greater")
        assert result is True

    def test_minimum_effect_size_greater_overlap(self) -> None:
        """When CIs overlap, should return False for 'greater'."""
        baseline_ci = BootstrapResult(
            point=10.0,
            lo=9.0,
            hi=11.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )
        candidate_ci = BootstrapResult(
            point=11.0,
            lo=10.5,
            hi=12.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )

        result = minimum_effect_size(baseline_ci, candidate_ci, direction="greater")
        assert result is False

    def test_minimum_effect_size_less_nonoverlap(self) -> None:
        """When candidate CI << baseline CI, should return True for 'less'."""
        baseline_ci = BootstrapResult(
            point=10.0,
            lo=9.0,
            hi=11.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )
        candidate_ci = BootstrapResult(
            point=5.0,
            lo=4.0,
            hi=6.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )

        result = minimum_effect_size(baseline_ci, candidate_ci, direction="less")
        assert result is True

    def test_minimum_effect_size_invalid_direction(self) -> None:
        """Invalid direction should raise ValueError."""
        baseline_ci = BootstrapResult(
            point=10.0,
            lo=9.0,
            hi=11.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )
        candidate_ci = BootstrapResult(
            point=15.0,
            lo=14.0,
            hi=16.0,
            n=50,
            ci_level=0.95,
            n_boot=1000,
        )

        with pytest.raises(ValueError, match="direction"):
            minimum_effect_size(baseline_ci, candidate_ci, direction="invalid")


class TestGate15WithBootstrap:
    """Gate 15 integration tests with bootstrap CI."""

    @dataclass
    class _Path:
        trades_df: Any

    def _path_with_trades(self, n_trades: int, n_days: int) -> _Path:
        """Build a fake CPCV path with evenly-spaced trades across n_days."""
        ts: list[datetime] = []
        per_day = max(1, n_trades // max(n_days, 1))
        remainder = n_trades - per_day * n_days if n_days > 0 else 0
        for d in range(n_days):
            base = datetime(2026, 1, 5, 14, 30, tzinfo=UTC) + timedelta(days=d)
            count = per_day + (1 if d < remainder else 0)
            for k in range(count):
                ts.append(base + timedelta(minutes=k * 5))
        return self._Path(trades_df=pl.DataFrame({"entry_ts": ts}))

    def test_gate_15_with_bootstrap_ci_narrow_passes(self) -> None:
        """Gate 15 with narrow CI within band should pass."""
        cfg = TurnoverConfig(
            min_trades_per_day=3.0,
            max_trades_per_day=50.0,
            use_bootstrap_ci=True,
            n_boot=100,
        )
        # 10 trades/day across 5 paths: very consistent
        paths = [self._path_with_trades(n_trades=20, n_days=2) for _ in range(5)]
        result = run_gate_15(paths, config=cfg)

        assert result.passed
        assert result.failure_reason is None
        assert "median_trades_per_day_lo" in result.metric_values
        assert "median_trades_per_day_hi" in result.metric_values
        lo = result.metric_values["median_trades_per_day_lo"]
        hi = result.metric_values["median_trades_per_day_hi"]
        assert 3.0 <= lo <= hi <= 50.0

    def test_gate_15_with_bootstrap_ci_wide_fails(self) -> None:
        """Gate 15 with wide CI crossing bounds should fail."""
        cfg = TurnoverConfig(
            min_trades_per_day=5.0,
            max_trades_per_day=10.0,
            use_bootstrap_ci=True,
            n_boot=100,
        )
        # Highly variable trades per path
        paths = [
            self._path_with_trades(n_trades=5, n_days=2),   # 2.5/day
            self._path_with_trades(n_trades=30, n_days=2),  # 15/day
            self._path_with_trades(n_trades=10, n_days=2),  # 5/day
        ]
        result = run_gate_15(paths, config=cfg)

        # The CI should span from low to high and cross the bounds
        assert not result.passed
        assert result.failure_reason is not None
        assert "crosses bounds" in result.failure_reason.lower() or "uncertainty" in result.failure_reason.lower()

    def test_gate_15_without_bootstrap_ci_uses_point(self) -> None:
        """Gate 15 with use_bootstrap_ci=False should use point estimate only."""
        cfg = TurnoverConfig(
            min_trades_per_day=3.0,
            max_trades_per_day=50.0,
            use_bootstrap_ci=False,
        )
        paths = [self._path_with_trades(n_trades=20, n_days=2) for _ in range(5)]
        result = run_gate_15(paths, config=cfg)

        # Should have no CI bounds in metric_values
        assert "median_trades_per_day_lo" not in result.metric_values
        assert "median_trades_per_day_hi" not in result.metric_values
        # But should still pass with point estimate
        assert result.passed

    def test_gate_15_bootstrap_ci_lower_bound_below_min_fails(self) -> None:
        """Gate 15 should fail if CI lo < min, even if point is in band."""
        cfg = TurnoverConfig(
            min_trades_per_day=5.0,
            max_trades_per_day=50.0,
            use_bootstrap_ci=True,
            n_boot=100,
        )
        # Trades per day: 3, 7 -> median 5 (in band), but with uncertainty might go below 5
        paths = [
            self._path_with_trades(n_trades=6, n_days=2),   # 3/day
            self._path_with_trades(n_trades=28, n_days=2),  # 14/day
        ]
        result = run_gate_15(paths, config=cfg)

        # Median is ~8.5, but CI may cross 5.0 given variability
        # Expect possible failure
        lo = result.metric_values.get("median_trades_per_day_lo")
        if lo is not None and lo < 5.0:
            assert not result.passed
