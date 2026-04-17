"""Unit tests for ShadowVenue Batch 4B realism features.

Tests the slippage models, latency models, partial-fill models, and
position-limit rejection logic added in Batch 4B.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mnq.core.types import OrderType, Side, Signal
from mnq.venues.shadow import (
    FixedLatency,
    FixedTickSlippage,
    FullFill,
    ShadowVenue,
    StochasticLatency,
    StochasticPartialFill,
    StochasticSlippage,
    ZeroLatency,
    ZeroSlippage,
)


def _mk_signal(
    side: Side = Side.LONG,
    qty: int = 1,
    ref: Decimal = Decimal("24000"),
) -> Signal:
    stop_distance = Decimal("10")
    tp_distance = Decimal("20")
    if side is Side.LONG:
        stop = ref - stop_distance
        tp = ref + tp_distance
    else:
        stop = ref + stop_distance
        tp = ref - tp_distance
    return Signal(
        side=side,
        qty=qty,
        ref_price=ref,
        stop=stop,
        take_profit=tp,
        order_type=OrderType.MARKET,
        spec_hash="test4b",
    )


def _ts(n: int = 0) -> datetime:
    return datetime(2026, 4, 16, 14, 30, tzinfo=UTC).replace(minute=30 + n)


# =====================================================================
# Slippage models
# =====================================================================


class TestZeroSlippage:
    def test_returns_zero(self) -> None:
        s = ZeroSlippage()
        assert s.ticks(Side.LONG, Decimal("24000"), 1) == Decimal(0)
        assert s.ticks(Side.SHORT, Decimal("24000"), 1) == Decimal(0)


class TestFixedTickSlippage:
    def test_default_one_tick(self) -> None:
        s = FixedTickSlippage()
        assert s.ticks(Side.LONG, Decimal("24000"), 1) == Decimal("0.25")

    def test_multi_tick(self) -> None:
        s = FixedTickSlippage(tick_count=3)
        assert s.ticks(Side.LONG, Decimal("24000"), 1) == Decimal("0.75")

    def test_custom_tick_size(self) -> None:
        s = FixedTickSlippage(tick_count=2, tick_size=Decimal("0.50"))
        assert s.ticks(Side.LONG, Decimal("24000"), 1) == Decimal("1.00")


class TestStochasticSlippage:
    def test_deterministic_with_same_seed(self) -> None:
        import random

        s1 = StochasticSlippage(_rng=random.Random(99))
        s2 = StochasticSlippage(_rng=random.Random(99))
        for _ in range(10):
            a = s1.ticks(Side.LONG, Decimal("24000"), 1)
            b = s2.ticks(Side.LONG, Decimal("24000"), 1)
            assert a == b

    def test_non_negative(self) -> None:
        import random

        s = StochasticSlippage(_rng=random.Random(0))
        for _ in range(50):
            assert s.ticks(Side.LONG, Decimal("24000"), 1) >= 0

    def test_clamped_to_max(self) -> None:
        import random

        s = StochasticSlippage(mean_ticks=100, max_ticks=2, _rng=random.Random(1))
        for _ in range(20):
            t = s.ticks(Side.LONG, Decimal("24000"), 1)
            assert t <= Decimal("0.50")  # max_ticks=2 × 0.25


# =====================================================================
# Slippage integration (venue)
# =====================================================================


class TestSlippageIntegration:
    def test_long_pays_more_with_slippage(self) -> None:
        venue = ShadowVenue(slippage=FixedTickSlippage(tick_count=2))
        result = venue.place_order(
            _mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts()
        )
        assert result.fill.price == Decimal("24000.50")  # +0.50
        assert result.slippage_ticks == Decimal("0.50")

    def test_short_receives_less_with_slippage(self) -> None:
        venue = ShadowVenue(slippage=FixedTickSlippage(tick_count=2))
        result = venue.place_order(
            _mk_signal(Side.SHORT), at_price=Decimal("24000"), at_ts=_ts()
        )
        assert result.fill.price == Decimal("23999.50")  # -0.50
        assert result.slippage_ticks == Decimal("0.50")

    def test_zero_slippage_no_price_change(self) -> None:
        venue = ShadowVenue(slippage=ZeroSlippage())
        result = venue.place_order(
            _mk_signal(), at_price=Decimal("24000"), at_ts=_ts()
        )
        assert result.fill.price == Decimal("24000")
        assert result.slippage_ticks == Decimal(0)


# =====================================================================
# Latency models
# =====================================================================


class TestZeroLatency:
    def test_returns_zero(self) -> None:
        assert ZeroLatency().delay() == timedelta(0)


class TestFixedLatency:
    def test_50ms(self) -> None:
        lat = FixedLatency(ms=50)
        assert lat.delay() == timedelta(milliseconds=50)


class TestStochasticLatency:
    def test_deterministic(self) -> None:
        import random

        a = StochasticLatency(_rng=random.Random(7))
        b = StochasticLatency(_rng=random.Random(7))
        for _ in range(10):
            assert a.delay() == b.delay()

    def test_positive(self) -> None:
        import random

        lat = StochasticLatency(_rng=random.Random(0))
        for _ in range(50):
            assert lat.delay() >= timedelta(0)


class TestLatencyIntegration:
    def test_fill_ts_shifted_forward(self) -> None:
        base = _ts(0)
        venue = ShadowVenue(latency=FixedLatency(ms=100))
        result = venue.place_order(
            _mk_signal(), at_price=Decimal("24000"), at_ts=base
        )
        assert result.fill.ts == base + timedelta(milliseconds=100)
        assert result.latency_ms == 100.0

    def test_zero_latency_no_shift(self) -> None:
        base = _ts(0)
        venue = ShadowVenue(latency=ZeroLatency())
        result = venue.place_order(
            _mk_signal(), at_price=Decimal("24000"), at_ts=base
        )
        assert result.fill.ts == base
        assert result.latency_ms == 0.0


# =====================================================================
# Partial fill models
# =====================================================================


class TestFullFill:
    def test_always_fills_full(self) -> None:
        ff = FullFill()
        for qty in [1, 2, 5, 10]:
            assert ff.filled_qty(qty) == qty


class TestStochasticPartialFill:
    def test_single_qty_never_reduced(self) -> None:
        import random

        pf = StochasticPartialFill(partial_prob=1.0, _rng=random.Random(0))
        # qty=1 can't be reduced below 1
        for _ in range(50):
            assert pf.filled_qty(1) == 1

    def test_partial_fill_reduces_qty(self) -> None:
        import random

        pf = StochasticPartialFill(
            partial_prob=1.0, min_fill_pct=0.5, _rng=random.Random(0)
        )
        results = [pf.filled_qty(10) for _ in range(30)]
        # With 100% partial prob and min_fill_pct=0.5, expect some < 10
        assert any(r < 10 for r in results)
        # All should be >= 5 (50% of 10)
        assert all(r >= 5 for r in results)

    def test_zero_prob_always_full(self) -> None:
        import random

        pf = StochasticPartialFill(partial_prob=0.0, _rng=random.Random(0))
        for _ in range(30):
            assert pf.filled_qty(10) == 10


class TestPartialFillIntegration:
    def test_fill_marked_partial(self) -> None:
        import random

        pf = StochasticPartialFill(
            partial_prob=1.0, min_fill_pct=0.5, _rng=random.Random(0)
        )
        venue = ShadowVenue(partial_fill=pf)
        result = venue.place_order(
            _mk_signal(qty=10), at_price=Decimal("24000"), at_ts=_ts()
        )
        assert result.requested_qty == 10
        # The fill.qty may or may not be 10 depending on RNG, but is_partial
        # flag should be correct
        if result.fill.qty < 10:
            assert result.fill.is_partial is True
        else:
            assert result.fill.is_partial is False


# =====================================================================
# Position-limit rejection
# =====================================================================


class TestPositionLimitRejection:
    def test_rejects_when_breaching_limit(self) -> None:
        venue = ShadowVenue(max_position_qty=2)
        # Fill 2 longs → net=+2
        venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(0))
        venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(1))
        assert venue.net_qty == 2
        # Third long should be rejected
        r = venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(2))
        assert r.rejected is True
        assert "position_limit" in r.reject_reason
        assert r.fill.qty == 0
        assert venue.n_rejected == 1

    def test_opposite_direction_allowed(self) -> None:
        venue = ShadowVenue(max_position_qty=2)
        venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(0))
        venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(1))
        # Short reduces net position, so it should be allowed
        r = venue.place_order(_mk_signal(Side.SHORT), at_price=Decimal("24000"), at_ts=_ts(2))
        assert r.rejected is False
        assert venue.net_qty == 1

    def test_no_limit_allows_unlimited(self) -> None:
        venue = ShadowVenue(max_position_qty=None)
        for i in range(20):
            r = venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(i))
            assert r.rejected is False
        assert venue.net_qty == 20

    def test_short_position_limit_works(self) -> None:
        venue = ShadowVenue(max_position_qty=1)
        venue.place_order(_mk_signal(Side.SHORT), at_price=Decimal("24000"), at_ts=_ts(0))
        assert venue.net_qty == -1
        r = venue.place_order(_mk_signal(Side.SHORT), at_price=Decimal("24000"), at_ts=_ts(1))
        assert r.rejected is True

    def test_get_rejections_returns_history(self) -> None:
        venue = ShadowVenue(max_position_qty=1)
        venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(0))
        venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(1))
        rejs = venue.get_rejections()
        assert len(rejs) == 1
        assert rejs[0].rejected is True


# =====================================================================
# Combined realism (slippage + latency + partial fill)
# =====================================================================


class TestCombinedRealism:
    def test_all_models_compose(self) -> None:
        import random

        venue = ShadowVenue(
            slippage=FixedTickSlippage(tick_count=1),
            latency=FixedLatency(ms=50),
            partial_fill=StochasticPartialFill(
                partial_prob=1.0, min_fill_pct=0.5, _rng=random.Random(0)
            ),
            max_position_qty=100,
        )
        base = _ts(0)
        result = venue.place_order(
            _mk_signal(Side.LONG, qty=10), at_price=Decimal("24000"), at_ts=base
        )
        # Slippage applied
        assert result.fill.price >= Decimal("24000")
        assert result.slippage_ticks == Decimal("0.25")
        # Latency applied
        assert result.fill.ts == base + timedelta(milliseconds=50)
        # Partial fill may reduce
        assert result.fill.qty >= 5
        assert result.fill.qty <= 10

    def test_backward_compat_4a_defaults(self) -> None:
        """A venue with no models behaves exactly like the 4A scaffold."""
        venue = ShadowVenue()
        base = _ts(0)
        r = venue.place_order(
            _mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=base
        )
        assert r.fill.price == Decimal("24000")
        assert r.fill.ts == base
        assert r.fill.qty == 1
        assert r.fill.is_partial is False
        assert r.rejected is False
        assert r.slippage_ticks == Decimal(0)
        assert r.latency_ms == 0.0
