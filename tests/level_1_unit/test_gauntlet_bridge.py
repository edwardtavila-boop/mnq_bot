"""Tests for mnq.gauntlet.bridge — context_from_bars."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mnq.core.types import Bar, Side
from mnq.gauntlet.bridge import _ema, context_from_bars, normalize_regime
from mnq.gauntlet.gates.gauntlet12 import GauntletContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    ts: datetime,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    volume: int = 100,
) -> Bar:
    c = Decimal(str(close))
    h = Decimal(str(high)) if high is not None else c + Decimal("1.00")
    lo = Decimal(str(low)) if low is not None else c - Decimal("1.00")
    return Bar(
        ts=ts,
        open=c,
        high=h,
        low=lo,
        close=c,
        volume=volume,
        timeframe_sec=60,
    )


def _bar_series(n: int = 40, base_price: float = 20000.0) -> list[Bar]:
    """Generate n bars with incrementing closes."""
    t0 = datetime(2025, 1, 2, 14, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for i in range(n):
        price = base_price + i * 0.25
        bars.append(_make_bar(t0 + timedelta(minutes=i), price, volume=100 + i))
    return bars


# ---------------------------------------------------------------------------
# _ema helper
# ---------------------------------------------------------------------------

class TestEma:
    def test_short_input_returns_none(self) -> None:
        assert _ema([1.0, 2.0], span=5) is None

    def test_span_1_returns_last(self) -> None:
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = _ema(vals, span=1)
        assert result is not None
        # span=1 → k=1.0, so EMA == last value
        assert abs(result - 5.0) < 1e-9

    def test_known_ema(self) -> None:
        vals = [10.0, 11.0, 12.0]
        # span=3 → k=0.5
        # ema[0] = 10
        # ema[1] = 11*0.5 + 10*0.5 = 10.5
        # ema[2] = 12*0.5 + 10.5*0.5 = 11.25
        result = _ema(vals, span=3)
        assert result is not None
        assert abs(result - 11.25) < 1e-9

    def test_exact_span_works(self) -> None:
        vals = [5.0, 6.0, 7.0]
        assert _ema(vals, span=3) is not None


# ---------------------------------------------------------------------------
# context_from_bars — basic construction
# ---------------------------------------------------------------------------

class TestContextFromBars:
    def test_returns_gauntlet_context(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, side="long")
        assert isinstance(ctx, GauntletContext)

    def test_side_string(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, side="short")
        assert ctx.side == "short"

    def test_side_enum(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, side=Side.LONG)
        assert ctx.side == "long"

    def test_closes_length_bounded_by_lookback(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, lookback=10)
        # window = bars[25:36] → 11 bars
        assert len(ctx.closes) == 11

    def test_closes_length_when_fewer_bars(self) -> None:
        bars = _bar_series(5)
        ctx = context_from_bars(bars, bar_idx=2, lookback=30)
        # window = bars[0:3] → 3 bars
        assert len(ctx.closes) == 3

    def test_highs_lows_volumes_same_length(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35)
        assert len(ctx.highs) == len(ctx.closes)
        assert len(ctx.lows) == len(ctx.closes)
        assert len(ctx.volumes) == len(ctx.closes)

    def test_ema_fast_slow_populated(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35)
        assert ctx.ema_fast is not None
        assert ctx.ema_slow is not None

    def test_ema_prev_populated(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35)
        assert ctx.ema_fast_prev is not None
        assert ctx.ema_slow_prev is not None

    def test_ema_none_when_insufficient_bars(self) -> None:
        bars = _bar_series(5)
        ctx = context_from_bars(bars, bar_idx=2, ema_slow_span=21)
        # only 3 closes, can't compute EMA(21)
        assert ctx.ema_slow is None

    def test_now_equals_entry_bar_ts(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=10)
        assert ctx.now == bars[10].ts

    def test_bar_index_passthrough(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=15)
        assert ctx.bar_index == 15


# ---------------------------------------------------------------------------
# context_from_bars — external data passthrough
# ---------------------------------------------------------------------------

class TestExternalData:
    def test_loss_streak(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, loss_streak=3)
        assert ctx.loss_streak == 3

    def test_regime(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, regime="trend_up")
        assert ctx.regime == "trend_up"

    def test_intermarket_corr(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, intermarket_corr=0.85)
        assert ctx.intermarket_corr == 0.85

    def test_spread_ticks(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, spread_ticks=1.5)
        assert ctx.spread_ticks == 1.5

    def test_high_impact_events(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, high_impact_events_minutes=[5, 10])
        assert ctx.high_impact_events_minutes == [5, 10]

    def test_defaults_for_external_data(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35)
        assert ctx.loss_streak == 0
        assert ctx.regime is None
        assert ctx.intermarket_corr is None
        assert ctx.spread_ticks is None
        assert ctx.high_impact_events_minutes == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_bar_idx_zero(self) -> None:
        bars = _bar_series(10)
        ctx = context_from_bars(bars, bar_idx=0)
        assert len(ctx.closes) == 1
        assert ctx.now == bars[0].ts

    def test_bar_idx_last(self) -> None:
        bars = _bar_series(10)
        ctx = context_from_bars(bars, bar_idx=9)
        assert ctx.now == bars[9].ts

    def test_single_bar_ema_prev_none(self) -> None:
        bars = _bar_series(1)
        ctx = context_from_bars(bars, bar_idx=0)
        # Only 1 close → can't compute prior-bar EMAs
        assert ctx.ema_fast_prev is None
        assert ctx.ema_slow_prev is None

    def test_custom_ema_spans(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, ema_fast_span=5, ema_slow_span=10)
        # Both should compute fine with 31 bars
        assert ctx.ema_fast is not None
        assert ctx.ema_slow is not None

    def test_close_values_are_float(self) -> None:
        bars = _bar_series(10)
        ctx = context_from_bars(bars, bar_idx=5)
        assert all(isinstance(c, float) for c in ctx.closes)

    def test_volume_values_are_int(self) -> None:
        bars = _bar_series(10)
        ctx = context_from_bars(bars, bar_idx=5)
        assert all(isinstance(v, int) for v in ctx.volumes)


# ---------------------------------------------------------------------------
# Regime label normalization (Batch 5B)
# ---------------------------------------------------------------------------

class TestNormalizeRegime:
    def test_none_passthrough(self) -> None:
        assert normalize_regime(None) is None

    def test_real_trend_up(self) -> None:
        assert normalize_regime("real_trend_up") == "trend_up"

    def test_real_trend_down(self) -> None:
        assert normalize_regime("real_trend_down") == "trend_down"

    def test_real_chop(self) -> None:
        assert normalize_regime("real_chop") == "chop"

    def test_real_high_vol(self) -> None:
        assert normalize_regime("real_high_vol") == "high_vol"

    def test_real_range(self) -> None:
        assert normalize_regime("real_range") == "range"

    def test_native_labels_unchanged(self) -> None:
        assert normalize_regime("trend_up") == "trend_up"
        assert normalize_regime("trend_down") == "trend_down"
        assert normalize_regime("chop") == "chop"

    def test_unknown_label_passthrough(self) -> None:
        # Unknown labels pass through as-is (regime gate handles gracefully)
        assert normalize_regime("exotic_regime") == "exotic_regime"


class TestRegimeInContext:
    def test_real_label_normalized_in_context(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, regime="real_trend_up")
        assert ctx.regime == "trend_up"

    def test_real_chop_normalized(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, regime="real_chop")
        assert ctx.regime == "chop"

    def test_none_regime_stays_none(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, regime=None)
        assert ctx.regime is None

    def test_native_label_preserved(self) -> None:
        bars = _bar_series(40)
        ctx = context_from_bars(bars, bar_idx=35, regime="trend_down")
        assert ctx.regime == "trend_down"
