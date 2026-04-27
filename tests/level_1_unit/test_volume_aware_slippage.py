"""Tests for VolumeAwareSlippage — Batch 6C volume-dependent slippage model."""

from __future__ import annotations

import random
from decimal import Decimal

from mnq.core.types import Side
from mnq.venues.shadow import VolumeAwareSlippage


class TestVolumeAwareSlippage:
    def test_returns_decimal(self) -> None:
        model = VolumeAwareSlippage()
        result = model.ticks(Side.LONG, Decimal("20000"), 1)
        assert isinstance(result, Decimal)

    def test_non_negative(self) -> None:
        model = VolumeAwareSlippage(_rng=random.Random(0))
        for qty in [1, 2, 5, 10, 50]:
            result = model.ticks(Side.LONG, Decimal("20000"), qty)
            assert result >= 0

    def test_larger_qty_more_slippage(self) -> None:
        """More contracts → more slippage (on average)."""
        model_small = VolumeAwareSlippage(_rng=random.Random(99))
        model_large = VolumeAwareSlippage(_rng=random.Random(99))
        small = model_small.ticks(Side.LONG, Decimal("20000"), 1)
        large = model_large.ticks(Side.LONG, Decimal("20000"), 50)
        assert large >= small

    def test_one_lot_minimal(self) -> None:
        """1-lot order at default 50-lot depth should get <= 2 ticks."""
        model = VolumeAwareSlippage(_rng=random.Random(42))
        result = model.ticks(Side.LONG, Decimal("20000"), 1)
        assert result <= Decimal("0.50")  # 2 ticks

    def test_clamped_to_max(self) -> None:
        """Even huge orders can't exceed max_ticks."""
        model = VolumeAwareSlippage(max_ticks=3, _rng=random.Random(42))
        result = model.ticks(Side.LONG, Decimal("20000"), 1000)
        assert result <= Decimal(str(3 * 0.25))

    def test_deterministic_with_same_seed(self) -> None:
        m1 = VolumeAwareSlippage(_rng=random.Random(7))
        m2 = VolumeAwareSlippage(_rng=random.Random(7))
        r1 = m1.ticks(Side.LONG, Decimal("20000"), 5)
        r2 = m2.ticks(Side.LONG, Decimal("20000"), 5)
        assert r1 == r2

    def test_different_seed_different_result(self) -> None:
        """Different seeds should (usually) produce different noise on large orders."""
        m1 = VolumeAwareSlippage(_rng=random.Random(1))
        m2 = VolumeAwareSlippage(_rng=random.Random(2))
        # Use large qty so base slip is high enough that noise causes rounding differences
        results1 = [m1.ticks(Side.LONG, Decimal("20000"), 100) for _ in range(20)]
        results2 = [m2.ticks(Side.LONG, Decimal("20000"), 100) for _ in range(20)]
        assert results1 != results2

    def test_tick_size_respected(self) -> None:
        """Output should be a multiple of tick_size."""
        model = VolumeAwareSlippage(tick_size=Decimal("0.25"), _rng=random.Random(42))
        for qty in [1, 5, 10]:
            result = model.ticks(Side.LONG, Decimal("20000"), qty)
            # result / 0.25 should be an integer
            assert result % Decimal("0.25") == 0

    def test_custom_depth(self) -> None:
        """Shallower book → more slippage for same qty."""
        deep = VolumeAwareSlippage(depth_lots=100, _rng=random.Random(42))
        shallow = VolumeAwareSlippage(depth_lots=10, _rng=random.Random(42))
        r_deep = deep.ticks(Side.LONG, Decimal("20000"), 5)
        r_shallow = shallow.ticks(Side.LONG, Decimal("20000"), 5)
        assert r_shallow >= r_deep

    def test_zero_depth_no_crash(self) -> None:
        """depth_lots=0 shouldn't crash."""
        model = VolumeAwareSlippage(depth_lots=0, _rng=random.Random(42))
        result = model.ticks(Side.LONG, Decimal("20000"), 1)
        assert result >= 0

    def test_side_irrelevant_for_tick_count(self) -> None:
        """VolumeAwareSlippage returns ticks, not direction. Side doesn't matter."""
        model_l = VolumeAwareSlippage(_rng=random.Random(42))
        model_s = VolumeAwareSlippage(_rng=random.Random(42))
        assert model_l.ticks(Side.LONG, Decimal("20000"), 5) == model_s.ticks(
            Side.SHORT, Decimal("20000"), 5
        )
