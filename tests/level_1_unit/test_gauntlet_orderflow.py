"""Tests for mnq.gauntlet.orderflow — Bookmap/DOM order flow features."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mnq.core.types import Bar
from mnq.gauntlet.orderflow import (
    DepthSnapshot,
    OrderFlowSnapshot,
    OrderFlowTracker,
    depth_aware_slippage_ticks,
    orderflow_from_bars,
)


def _make_bar(
    ts: datetime,
    o: float,
    h: float,
    lo: float,
    c: float,
    volume: int = 100,
) -> Bar:
    return Bar(
        ts=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=volume,
        timeframe_sec=60,
    )


T0 = datetime(2025, 1, 2, 14, 0, tzinfo=UTC)


class TestOrderFlowSnapshot:
    def test_frozen(self) -> None:
        snap = OrderFlowSnapshot(
            bar_delta=10.0,
            cvd=10.0,
            imbalance=0.5,
            absorption_score=0.3,
            buy_aggressor_pct=0.7,
            bid_depth=0.0,
            ask_depth=0.0,
            is_live=False,
        )
        assert snap.bar_delta == 10.0
        assert snap.is_live is False


class TestOrderFlowTracker:
    def test_bullish_bar_positive_delta(self) -> None:
        tracker = OrderFlowTracker()
        # Close at high → strong buyer delta
        bar = _make_bar(T0, 20000, 20002, 19998, 20002, volume=200)
        snap = tracker.on_bar(bar)
        assert snap.bar_delta > 0
        assert snap.cvd > 0

    def test_bearish_bar_negative_delta(self) -> None:
        tracker = OrderFlowTracker()
        # Close at low → strong seller delta
        bar = _make_bar(T0, 20000, 20002, 19998, 19998, volume=200)
        snap = tracker.on_bar(bar)
        assert snap.bar_delta < 0

    def test_doji_bar_near_zero_delta(self) -> None:
        tracker = OrderFlowTracker()
        # Close at midpoint → near-zero delta
        bar = _make_bar(T0, 20000, 20002, 19998, 20000, volume=200)
        snap = tracker.on_bar(bar)
        assert abs(snap.bar_delta) < 1.0

    def test_cvd_accumulates(self) -> None:
        tracker = OrderFlowTracker()
        bars = [
            _make_bar(T0 + timedelta(minutes=i), 20000, 20002, 19998, 20002, volume=100)
            for i in range(5)
        ]
        snaps = [tracker.on_bar(b) for b in bars]
        # Each bar is bullish → CVD should grow
        assert snaps[-1].cvd > snaps[0].cvd

    def test_imbalance_bullish(self) -> None:
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 20000, 20002, 19998, 20002, volume=200)
        snap = tracker.on_bar(bar)
        assert snap.imbalance > 0

    def test_imbalance_bearish(self) -> None:
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 20000, 20002, 19998, 19998, volume=200)
        snap = tracker.on_bar(bar)
        assert snap.imbalance < 0

    def test_imbalance_in_range(self) -> None:
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 20000, 20002, 19998, 20001, volume=200)
        snap = tracker.on_bar(bar)
        assert -1.0 <= snap.imbalance <= 1.0

    def test_absorption_high_for_doji(self) -> None:
        """Doji (small body, big range) → high absorption score."""
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 20000, 20005, 19995, 20000.50, volume=200)
        snap = tracker.on_bar(bar)
        assert snap.absorption_score > 0.9  # body is tiny vs range

    def test_absorption_low_for_marubozu(self) -> None:
        """Marubozu (body = range) → low absorption score."""
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 19998, 20002, 19998, 20002, volume=200)
        snap = tracker.on_bar(bar)
        assert snap.absorption_score == 0.0

    def test_buy_aggressor_pct_bullish(self) -> None:
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 20000, 20002, 19998, 20002, volume=200)
        snap = tracker.on_bar(bar)
        assert snap.buy_aggressor_pct == 1.0

    def test_buy_aggressor_pct_bearish(self) -> None:
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 20000, 20002, 19998, 19998, volume=200)
        snap = tracker.on_bar(bar)
        assert snap.buy_aggressor_pct == 0.0

    def test_reset_session(self) -> None:
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 20000, 20002, 19998, 20002, volume=200)
        tracker.on_bar(bar)
        assert tracker.cvd != 0.0
        tracker.reset_session()
        assert tracker.cvd == 0.0

    def test_cvd_slope_increasing(self) -> None:
        tracker = OrderFlowTracker()
        for i in range(5):
            bar = _make_bar(
                T0 + timedelta(minutes=i), 20000, 20002, 19998, 20002, volume=100 + i * 20
            )
            tracker.on_bar(bar)
        # Increasing volume with same direction → positive slope
        assert tracker.cvd_slope > 0

    def test_zero_volume_bar(self) -> None:
        tracker = OrderFlowTracker()
        bar = _make_bar(T0, 20000, 20000, 20000, 20000, volume=0)
        snap = tracker.on_bar(bar)
        assert snap.bar_delta == 0.0
        assert snap.buy_aggressor_pct == 0.5


class TestDepthSnapshot:
    def test_best_bid_ask(self) -> None:
        depth = DepthSnapshot(
            bids=[(20000.00, 50), (19999.75, 30)],
            asks=[(20000.25, 40), (20000.50, 20)],
        )
        assert depth.best_bid == 20000.00
        assert depth.best_ask == 20000.25

    def test_spread_ticks(self) -> None:
        depth = DepthSnapshot(
            bids=[(20000.00, 50)],
            asks=[(20000.25, 40)],
        )
        assert depth.spread_ticks == 1.0

    def test_imbalance_even(self) -> None:
        depth = DepthSnapshot(
            bids=[(20000.00, 50)],
            asks=[(20000.25, 50)],
        )
        assert depth.imbalance == 0.0

    def test_imbalance_bid_heavy(self) -> None:
        depth = DepthSnapshot(
            bids=[(20000.00, 100)],
            asks=[(20000.25, 20)],
        )
        assert depth.imbalance > 0

    def test_empty_book(self) -> None:
        depth = DepthSnapshot(bids=[], asks=[])
        assert depth.best_bid == 0.0
        assert depth.imbalance == 0.0
        assert depth.spread_ticks == 0.0


class TestLiveMode:
    def test_on_tick_updates_delta(self) -> None:
        tracker = OrderFlowTracker()
        tracker.on_tick(20000.25, 10, is_buy=True)
        tracker.on_tick(20000.00, 5, is_buy=False)
        bar = _make_bar(T0, 20000, 20001, 19999, 20000.5, volume=15)
        snap = tracker.on_bar(bar)
        assert snap.is_live is True
        assert snap.bar_delta == 5.0  # 10 - 5

    def test_on_dom_update(self) -> None:
        tracker = OrderFlowTracker()
        depth = DepthSnapshot(
            bids=[(20000.00, 100), (19999.75, 50)],
            asks=[(20000.25, 30), (20000.50, 20)],
        )
        tracker.on_dom_update(depth)
        bar = _make_bar(T0, 20000, 20001, 19999, 20000.5, volume=50)
        snap = tracker.on_bar(bar)
        assert snap.is_live is True
        assert snap.bid_depth == 150  # 100 + 50
        assert snap.ask_depth == 50  # 30 + 20


class TestOrderflowFromBars:
    def test_empty_bars(self) -> None:
        snap = orderflow_from_bars([])
        assert snap.bar_delta == 0.0
        assert snap.is_live is False

    def test_single_bar(self) -> None:
        bar = _make_bar(T0, 20000, 20002, 19998, 20001, volume=100)
        snap = orderflow_from_bars([bar])
        assert isinstance(snap, OrderFlowSnapshot)

    def test_eval_at_specific_bar(self) -> None:
        bars = [
            _make_bar(T0 + timedelta(minutes=i), 20000, 20002, 19998, 20002, volume=100)
            for i in range(10)
        ]
        snap_early = orderflow_from_bars(bars, eval_bar_idx=2)
        snap_late = orderflow_from_bars(bars, eval_bar_idx=9)
        # More bars processed → more CVD accumulated
        assert snap_late.cvd > snap_early.cvd

    def test_eval_bar_idx_clamped(self) -> None:
        bars = [_make_bar(T0, 20000, 20002, 19998, 20001, volume=100)]
        # idx beyond range should clamp
        snap = orderflow_from_bars(bars, eval_bar_idx=999)
        assert isinstance(snap, OrderFlowSnapshot)


class TestDepthAwareSlippage:
    def test_no_depth_fallback(self) -> None:
        ticks = depth_aware_slippage_ticks(1, None)
        assert ticks > 0

    def test_larger_qty_more_slippage_no_depth(self) -> None:
        small = depth_aware_slippage_ticks(1, None)
        large = depth_aware_slippage_ticks(50, None)
        assert large > small

    def test_with_depth_walks_book(self) -> None:
        depth = DepthSnapshot(
            bids=[(20000.00, 10), (19999.75, 10), (19999.50, 10)],
            asks=[(20000.25, 10), (20000.50, 10), (20000.75, 10)],
        )
        small = depth_aware_slippage_ticks(5, depth)
        large = depth_aware_slippage_ticks(25, depth)
        assert large > small

    def test_deep_book_less_slippage(self) -> None:
        shallow = DepthSnapshot(
            bids=[(20000.00, 5)],
            asks=[(20000.25, 5)],
        )
        deep = DepthSnapshot(
            bids=[(20000.00, 500), (19999.75, 500)],
            asks=[(20000.25, 500), (20000.50, 500)],
        )
        slip_shallow = depth_aware_slippage_ticks(10, shallow)
        slip_deep = depth_aware_slippage_ticks(10, deep)
        assert slip_shallow > slip_deep
