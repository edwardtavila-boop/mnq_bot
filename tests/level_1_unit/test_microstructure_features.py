"""[REAL] Level-1 unit tests for mnq.features.microstructure.

These features are streaming-friendly — `.update(bar)` once per bar.
Reference-style tests lock in:

    * warmup semantics (None until window fills)
    * signal direction under controlled synthetic tape
    * numerical stability under edge cases (flat bars, zero volume,
      zero-price, single-direction run)
    * bounds where the math guarantees them (entropy ∈ [0, 1],
      autocorrelation ∈ [-1, +1])

This is deliberately cheap — no I/O — so CI runs it on every change.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from mnq.features import (
    BarImbalance,
    BarReturnAutocorrelation,
    LiquidityAbsorption,
    VolumeEntropy,
)
from tests.level_1_unit._bars import constant_bars, make_bar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hlc_bar(
    ts: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 100,
    tf_sec: int = 60,
):
    """Build a bar from explicit OHLCV."""
    return make_bar(ts, open_, high, low, close, volume, tf_sec)


def _volatile_bars(
    n: int,
    price: float = 20000.0,
    amp: float = 5.0,
    volume: int = 100,
    tf_sec: int = 60,
):
    """Bars with a deterministic zigzag — up, down, up, down …

    Close alternates between ``price + amp`` and ``price - amp``.
    Each bar's range covers both extremes so the feature can read the shape.
    """
    start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    out = []
    for i in range(n):
        t = start + timedelta(seconds=tf_sec * i)
        c = price + amp if i % 2 == 0 else price - amp
        out.append(_hlc_bar(t, price, price + amp, price - amp, c, volume, tf_sec))
    return out


def _trending_bars(
    n: int, start_price: float = 20000.0, step: float = 1.0, volume: int = 100, tf_sec: int = 60
):
    """Monotone-up trending bars — each close > previous close."""
    start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    out = []
    for i in range(n):
        t = start + timedelta(seconds=tf_sec * i)
        c = start_price + step * i
        out.append(_hlc_bar(t, c - 0.25, c + 0.25, c - 0.5, c, volume, tf_sec))
    return out


# ---------------------------------------------------------------------------
# C1 — BarImbalance
# ---------------------------------------------------------------------------
class TestBarImbalance:
    def test_rejects_too_short(self) -> None:
        with pytest.raises(ValueError):
            BarImbalance(length=4)

    def test_warmup_returns_none(self) -> None:
        f = BarImbalance(length=20)
        for b in _volatile_bars(10):
            assert f.update(b) is None
        assert not f.ready

    def test_ready_after_window(self) -> None:
        f = BarImbalance(length=10)
        for b in _volatile_bars(11):
            f.update(b)
        assert f.ready
        assert f.value is not None

    def test_flat_bars_yield_zero(self) -> None:
        """All bars identical ⇒ raw = 0 ⇒ z = 0 (σ=0 fallback)."""
        f = BarImbalance(length=10)
        for b in constant_bars(20, price=100.0):
            f.update(b)
        assert f.value == pytest.approx(0.0)

    def test_doji_bar_has_zero_raw(self) -> None:
        """high == low ⇒ no directional info ⇒ raw == 0."""
        f = BarImbalance(length=10)
        # 10 normal bars + 1 doji
        for b in _volatile_bars(10):
            f.update(b)
        t = datetime(2026, 1, 2, 10, 30, tzinfo=UTC)
        doji = _hlc_bar(t, 100.0, 100.0, 100.0, 100.0)
        f.update(doji)
        assert f.raw == 0.0

    def test_strong_up_bar_positive_z(self) -> None:
        """Close at bar high after a volatile preamble ⇒ z > 0."""
        f = BarImbalance(length=10)
        for b in _volatile_bars(20):
            f.update(b)
        # A clean up-bar: close exactly at high.
        t = datetime(2026, 1, 3, 9, 30, tzinfo=UTC)
        up_bar = _hlc_bar(t, 20000.0, 20010.0, 19990.0, 20010.0)
        f.update(up_bar)
        assert f.value is not None
        assert f.value > 0

    def test_strong_down_bar_negative_z(self) -> None:
        f = BarImbalance(length=10)
        for b in _volatile_bars(20):
            f.update(b)
        t = datetime(2026, 1, 3, 9, 30, tzinfo=UTC)
        down_bar = _hlc_bar(t, 20000.0, 20010.0, 19990.0, 19990.0)
        f.update(down_bar)
        assert f.value is not None
        assert f.value < 0

    def test_raw_bounded_in_unit_interval(self) -> None:
        """Raw imbalance must always satisfy -1 <= raw <= 1."""
        f = BarImbalance(length=10)
        for b in _volatile_bars(50):
            f.update(b)
            assert -1.0 <= f.raw <= 1.0


# ---------------------------------------------------------------------------
# C2 — VolumeEntropy
# ---------------------------------------------------------------------------
class TestVolumeEntropy:
    def test_rejects_too_short(self) -> None:
        with pytest.raises(ValueError):
            VolumeEntropy(length=2)

    def test_warmup(self) -> None:
        f = VolumeEntropy(length=10)
        for b in constant_bars(9, volume=100):
            assert f.update(b) is None
        assert not f.ready

    def test_uniform_volume_gives_unit_entropy(self) -> None:
        """Constant volume across the window ⇒ H_norm = 1.0."""
        f = VolumeEntropy(length=10)
        for b in constant_bars(20, volume=100):
            f.update(b)
        assert f.value == pytest.approx(1.0, abs=1e-12)

    def test_single_bar_dominates_gives_low_entropy(self) -> None:
        """One bar carries 100% of the volume ⇒ H = 0.

        We feed 9 zero-volume bars + 1 heavy bar.
        """
        f = VolumeEntropy(length=10)
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        for i in range(9):
            t = start + timedelta(seconds=60 * i)
            f.update(_hlc_bar(t, 100, 101, 99, 100, volume=0))
        t = start + timedelta(seconds=60 * 9)
        f.update(_hlc_bar(t, 100, 101, 99, 100, volume=1000))
        assert f.value is not None
        assert f.value == pytest.approx(0.0, abs=1e-9)

    def test_zero_volume_window_returns_one(self) -> None:
        """All-zero volume ⇒ uninformative ⇒ 1.0."""
        f = VolumeEntropy(length=5)
        for b in constant_bars(10, volume=0):
            f.update(b)
        assert f.value == pytest.approx(1.0)

    def test_entropy_is_bounded(self) -> None:
        """H_norm ∈ [0, 1] for any input."""
        f = VolumeEntropy(length=20)
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        # Mixed volumes
        for i in range(40):
            t = start + timedelta(seconds=60 * i)
            v = int((i * 37) % 500) + 1
            f.update(_hlc_bar(t, 100, 101, 99, 100, volume=v))
            if f.value is not None:
                assert 0.0 - 1e-12 <= f.value <= 1.0 + 1e-12


# ---------------------------------------------------------------------------
# C3 — LiquidityAbsorption
# ---------------------------------------------------------------------------
class TestLiquidityAbsorption:
    def test_rejects_too_short(self) -> None:
        with pytest.raises(ValueError):
            LiquidityAbsorption(length=4)

    def test_warmup(self) -> None:
        f = LiquidityAbsorption(length=10)
        for b in _volatile_bars(9):
            assert f.update(b) is None
        assert not f.ready

    def test_flat_tape_yields_zero_z(self) -> None:
        """Constant volume+range ⇒ every raw identical ⇒ σ=0 ⇒ z=0."""
        f = LiquidityAbsorption(length=10)
        # Every bar has volume=100, range=2.
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        for i in range(20):
            t = start + timedelta(seconds=60 * i)
            f.update(_hlc_bar(t, 100, 101, 99, 100, volume=100))
        assert f.value == pytest.approx(0.0)

    def test_absorption_spike_yields_positive_z(self) -> None:
        """Heavy volume in tight range after normal tape ⇒ z > 0."""
        f = LiquidityAbsorption(length=20)
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        # 20 normal bars: volume=100 range=2 ⇒ raw=50
        for i in range(20):
            t = start + timedelta(seconds=60 * i)
            f.update(_hlc_bar(t, 100, 101, 99, 100, volume=100))
        # Big absorption bar: volume=5000 range=0.25 ⇒ raw=20000
        t = start + timedelta(seconds=60 * 20)
        f.update(_hlc_bar(t, 100, 100.125, 99.875, 100, volume=5000))
        assert f.value is not None
        assert f.value > 3.0  # huge z

    def test_void_bar_yields_negative_z(self) -> None:
        """Thin volume + wide range after normal tape ⇒ z < 0."""
        f = LiquidityAbsorption(length=20)
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        for i in range(20):
            t = start + timedelta(seconds=60 * i)
            f.update(_hlc_bar(t, 100, 101, 99, 100, volume=100))
        t = start + timedelta(seconds=60 * 20)
        # volume=1 range=50 ⇒ raw=0.02, much less than 50
        f.update(_hlc_bar(t, 100, 125, 75, 100, volume=1))
        assert f.value is not None
        assert f.value < 0

    def test_range_floor_prevents_div_by_zero(self) -> None:
        """Doji bars (range=0) use the floor _RANGE_EPS — no crash."""
        f = LiquidityAbsorption(length=10)
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        for i in range(20):
            t = start + timedelta(seconds=60 * i)
            # identical high/low (doji)
            f.update(_hlc_bar(t, 100, 100, 100, 100, volume=100))
        assert f.value is not None  # did not crash


# ---------------------------------------------------------------------------
# C4 — BarReturnAutocorrelation
# ---------------------------------------------------------------------------
class TestBarReturnAutocorrelation:
    def test_rejects_too_short(self) -> None:
        with pytest.raises(ValueError):
            BarReturnAutocorrelation(length=9)

    def test_warmup(self) -> None:
        f = BarReturnAutocorrelation(length=20)
        for b in _trending_bars(10):
            f.update(b)
        assert not f.ready

    def test_trending_tape_positive_autocorr(self) -> None:
        """Strict monotone trend ⇒ every return positive ⇒ autocorr > 0."""
        f = BarReturnAutocorrelation(length=30)
        for b in _trending_bars(50, step=0.5):
            f.update(b)
        assert f.value is not None
        assert f.value > 0.0

    def test_mean_reverting_tape_negative_autocorr(self) -> None:
        """Alternating up-down closes ⇒ returns alternate in sign ⇒ ρ < 0."""
        f = BarReturnAutocorrelation(length=30)
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        # Bar i close alternates: 100, 101, 100, 101, ...
        for i in range(50):
            t = start + timedelta(seconds=60 * i)
            c = 100.0 if i % 2 == 0 else 101.0
            f.update(_hlc_bar(t, c, c + 0.25, c - 0.25, c))
        assert f.value is not None
        assert f.value < 0.0

    def test_autocorr_bounded(self) -> None:
        """ρ ∈ [-1, +1] for any streaming input."""
        f = BarReturnAutocorrelation(length=20)
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        for i in range(60):
            t = start + timedelta(seconds=60 * i)
            # pseudo-random walk
            c = 20000.0 + 2.5 * math.sin(i * 0.7) + 0.5 * ((i * 17) % 7 - 3)
            f.update(_hlc_bar(t, c, c + 1, c - 1, c))
            if f.value is not None:
                assert -1.0 - 1e-9 <= f.value <= 1.0 + 1e-9

    def test_constant_close_yields_zero_autocorr(self) -> None:
        """Zero-variance returns ⇒ denominator == 0 ⇒ we return 0."""
        f = BarReturnAutocorrelation(length=20)
        for b in constant_bars(30, price=20000.0):
            f.update(b)
        assert f.value == pytest.approx(0.0)

    def test_non_positive_close_does_not_crash(self) -> None:
        """Zero close after a positive close is treated as a no-op."""
        f = BarReturnAutocorrelation(length=20)
        start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        for i in range(10):
            t = start + timedelta(seconds=60 * i)
            f.update(_hlc_bar(t, 100, 101, 99, 100))
        # Bad bar: close = 0  → must not raise math.log(0)
        t_bad = start + timedelta(seconds=60 * 10)
        f.update(_hlc_bar(t_bad, 100, 101, 0, 0))
        # Recovery bar
        t_rec = start + timedelta(seconds=60 * 11)
        f.update(_hlc_bar(t_rec, 100, 101, 99, 100))
        # Must remain in bounds or None
        v = f.value
        assert v is None or -1.0 - 1e-9 <= v <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# Integration: all four features share the Feature contract
# ---------------------------------------------------------------------------
class TestMicrostructureFeatureContract:
    """All four features implement the same streaming contract."""

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: BarImbalance(length=10),
            lambda: VolumeEntropy(length=10),
            lambda: LiquidityAbsorption(length=10),
            lambda: BarReturnAutocorrelation(length=10),
        ],
    )
    def test_update_returns_value_or_none(self, factory) -> None:
        f = factory()
        for b in _volatile_bars(30):
            out = f.update(b)
            assert out is None or isinstance(out, float)

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: BarImbalance(length=10),
            lambda: VolumeEntropy(length=10),
            lambda: LiquidityAbsorption(length=10),
            lambda: BarReturnAutocorrelation(length=10),
        ],
    )
    def test_last_update_bar_ts_tracks_input(self, factory) -> None:
        f = factory()
        bars = _volatile_bars(20)
        for b in bars:
            f.update(b)
        assert f.last_update_bar_ts == bars[-1].ts

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: BarImbalance(length=10),
            lambda: VolumeEntropy(length=10),
            lambda: LiquidityAbsorption(length=10),
            lambda: BarReturnAutocorrelation(length=10),
        ],
    )
    def test_ready_matches_value_is_not_none(self, factory) -> None:
        f = factory()
        for b in _volatile_bars(30):
            f.update(b)
            assert f.ready == (f.value is not None)
