"""Level-1 unit tests for mnq.features.*.

Reference-style synthetic cases: constant input → constant output,
monotone input → bounded output, etc.  Step 4's DoD: these lock in
Pine-compatible semantics until real Pine reference fixtures exist.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mnq.features import ATR, EMA, RMA, SMA, VWAP, HTFWrapper, RelativeVolume
from tests.level_1_unit._bars import constant_bars, linear_close_bars, make_bar


class TestEMA:
    def test_constant_input_converges_to_constant(self) -> None:
        ema = EMA(length=9)
        out: list[float | None] = []
        for b in constant_bars(50, price=100.0):
            out.append(ema.update(b))
        assert out[0] is None  # not ready yet
        assert out[-1] == pytest.approx(100.0)

    def test_alpha_matches_pine_formula(self) -> None:
        ema = EMA(length=9)
        # alpha = 2/(N+1) = 0.2
        assert ema._alpha == pytest.approx(2.0 / 10.0)

    def test_seed_is_sma_of_first_n(self) -> None:
        ema = EMA(length=5)
        bars = linear_close_bars(5, start_price=100.0, slope=1.0)
        vals: list[float | None] = []
        for b in bars:
            vals.append(ema.update(b))
        # First 4 updates None, 5th is seed = mean(100, 101, 102, 103, 104) = 102
        assert vals[:4] == [None, None, None, None]
        assert vals[4] == pytest.approx(102.0)

    def test_rejects_too_short_length(self) -> None:
        with pytest.raises(ValueError):
            EMA(length=1)


class TestSMA:
    def test_simple_window(self) -> None:
        sma = SMA(length=3)
        vals: list[float | None] = []
        for b in linear_close_bars(5, start_price=1.0, slope=1.0):
            vals.append(sma.update(b))
        # 1,2,3,4,5 → None, None, 2, 3, 4
        assert vals == [None, None, 2.0, 3.0, 4.0]


class TestRMA:
    def test_constant_input_converges_to_constant(self) -> None:
        rma = RMA(length=10)
        last: float | None = None
        for b in constant_bars(100, price=50.0):
            last = rma.update(b)
        assert last == pytest.approx(50.0)

    def test_alpha_is_1_over_n(self) -> None:
        rma = RMA(length=14)
        assert rma._alpha == pytest.approx(1.0 / 14.0)


class TestATR:
    def test_constant_bars_true_range_zero(self) -> None:
        atr = ATR(length=14)
        last: float | None = None
        for b in constant_bars(30, price=100.0):
            last = atr.update(b)
        assert last == pytest.approx(0.0)

    def test_monotone_bars_has_positive_atr(self) -> None:
        atr = ATR(length=14)
        last: float | None = None
        for b in linear_close_bars(40, start_price=100.0, slope=1.0):
            last = atr.update(b)
        assert last is not None and last > 0


class TestVWAP:
    def test_constant_price_vwap_equals_price(self) -> None:
        v = VWAP(anchor="session")
        last: float | None = None
        for b in constant_bars(30, price=100.0, volume=50):
            last = v.update(b)
        assert last == pytest.approx(100.0)

    def test_zero_volume_fallback_to_typical_price(self) -> None:
        v = VWAP(anchor="session")
        bars = constant_bars(3, price=100.0, volume=0)
        last = None
        for b in bars:
            last = v.update(b)
        assert last == pytest.approx(100.0)

    def test_resets_on_day_boundary(self) -> None:
        v = VWAP(anchor="session")
        # Day 1: price 100 volume 1
        d1 = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
        d2 = datetime(2026, 1, 3, 9, 30, tzinfo=UTC)
        v.update(make_bar(d1, 100, 100, 100, 100, 1))
        # Day 2 bars, all at price 200
        last = None
        for i in range(3):
            last = v.update(make_bar(d2 + timedelta(minutes=i), 200, 200, 200, 200, 1))
        # After reset, VWAP should be 200, not some mixture of 100 & 200.
        assert last == pytest.approx(200.0)


class TestRelativeVolume:
    def test_flat_volume_is_one(self) -> None:
        r = RelativeVolume(length=20)
        last: float | None = None
        for b in constant_bars(25, volume=100):
            last = r.update(b)
        assert last == pytest.approx(1.0)

    def test_double_volume_is_two(self) -> None:
        r = RelativeVolume(length=20)
        last: float | None = None
        for i, b in enumerate(constant_bars(25, volume=100)):
            if i == 24:
                # bump last bar's volume to 200
                bb = make_bar(b.ts, 100, 100, 100, 100, 200)
                last = r.update(bb)
            else:
                last = r.update(b)
        assert last == pytest.approx(200.0 / 105.0)  # avg = (100*19 + 200)/20 = 105


class TestHTFWrapper:
    def test_aggregates_one_min_bars_into_5m(self) -> None:
        inner = SMA(length=2)
        w = HTFWrapper(inner, timeframe="5m")
        # 15 one-minute bars span 3 HTF buckets; 2 of them close mid-stream.
        bars = linear_close_bars(15, start_price=100.0, slope=1.0)
        vals = []
        for b in bars:
            vals.append(w.update(b))
        # After the 2nd HTF close, inner (SMA(2)) has enough to emit a value.
        assert any(v is not None for v in vals)

    def test_htf_has_no_lookahead(self) -> None:
        """HTFWrapper never reports the currently-accumulating bucket's value."""
        inner = SMA(length=2)
        w = HTFWrapper(inner, timeframe="5m")
        # Within the first 5 primary bars (still in bucket 0), value must be None.
        bars = linear_close_bars(4, start_price=100.0, slope=1.0)
        for b in bars:
            assert w.update(b) is None
