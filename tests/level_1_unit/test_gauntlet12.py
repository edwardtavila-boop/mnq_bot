"""Unit tests for the 12-gate gauntlet."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from mnq.gauntlet.gates.gauntlet12 import (
    GAUNTLET,
    GauntletContext,
    gate_correlation,
    gate_cross_mag,
    gate_news_window,
    gate_regime,
    gate_session,
    gate_spread,
    gate_streak,
    gate_time_of_day,
    gate_trend_align,
    gate_vol_band,
    gate_volume_confirm,
    run_gauntlet,
    verdict_summary,
)


def _clean_context() -> GauntletContext:
    closes = [
        21000.0 + i * 0.5 + 3.0 * math.sin(i * 0.7) + 2.0 * math.cos(i * 1.3) for i in range(60)
    ]
    return GauntletContext(
        now=datetime(2026, 4, 16, 14, 30, tzinfo=UTC),
        bar_index=60,
        side="long",
        closes=closes,
        highs=[c + 1 for c in closes],
        lows=[c - 1 for c in closes],
        volumes=[200] * 60,
        ema_fast=sum(closes[-9:]) / 9,
        ema_slow=sum(closes[-21:]) / 21,
        ema_fast_prev=sum(closes[-10:-1]) / 9,
        ema_slow_prev=sum(closes[-22:-1]) / 21,
        loss_streak=0,
        high_impact_events_minutes=[],
        regime="trend_up",
        intermarket_corr=0.85,
        spread_ticks=0.5,
    )


class TestGauntletShape:
    def test_exactly_12_gates(self):
        assert len(GAUNTLET) == 12

    def test_run_returns_12_verdicts(self):
        vs = run_gauntlet(_clean_context())
        assert len(vs) == 12

    def test_summary_aggregates_correctly(self):
        vs = run_gauntlet(_clean_context())
        s = verdict_summary(vs)
        assert s["n"] == 12
        assert s["passed"] + s["failed"] == 12
        assert 0.0 <= s["score"] <= 1.0


class TestSession:
    def test_rth_allows(self):
        ctx = _clean_context()  # 14:30 UTC = 10:30 ET
        assert gate_session(ctx).pass_ is True

    def test_lunch_denies(self):
        ctx = _clean_context()
        ctx.now = datetime(2026, 4, 16, 16, 30, tzinfo=UTC)
        assert gate_session(ctx).pass_ is False

    def test_off_hours_denies(self):
        ctx = _clean_context()
        ctx.now = datetime(2026, 4, 16, 6, 0, tzinfo=UTC)
        assert gate_session(ctx).pass_ is False


class TestTrendAlign:
    def test_aligned_uptrend_passes_long(self):
        ctx = _clean_context()
        assert gate_trend_align(ctx).pass_ is True

    def test_long_fails_in_downtrend(self):
        ctx = _clean_context()
        ctx.ema_fast, ctx.ema_fast_prev = 20990.0, 20995.0
        ctx.ema_slow, ctx.ema_slow_prev = 21000.0, 21002.0
        assert gate_trend_align(ctx).pass_ is False

    def test_missing_emas_fails_open(self):
        ctx = _clean_context()
        ctx.ema_fast = None
        r = gate_trend_align(ctx)
        assert r.pass_ is True
        assert "stub" in r.detail


class TestCrossMag:
    def test_large_magnitude_passes(self):
        ctx = _clean_context()
        ctx.ema_fast, ctx.ema_slow = 21050, 21000
        assert gate_cross_mag(ctx, min_mag=1.5).pass_ is True

    def test_tiny_magnitude_denies(self):
        ctx = _clean_context()
        ctx.ema_fast, ctx.ema_slow = 21000.5, 21000.0
        assert gate_cross_mag(ctx, min_mag=5.0).pass_ is False


class TestVolBand:
    def test_in_band_passes(self):
        assert gate_vol_band(_clean_context()).pass_ is True

    def test_too_volatile_denies(self):
        ctx = _clean_context()
        ctx.closes = [21000.0 + i * 100 for i in range(20)]  # huge variance
        assert gate_vol_band(ctx).pass_ is False


class TestStreak:
    def test_clean_passes(self):
        ctx = _clean_context()
        ctx.loss_streak = 0
        assert gate_streak(ctx, max_streak=3).pass_ is True

    def test_too_many_losses_denies(self):
        ctx = _clean_context()
        ctx.loss_streak = 4
        assert gate_streak(ctx, max_streak=3).pass_ is False


class TestNewsWindow:
    def test_no_events_passes(self):
        ctx = _clean_context()
        ctx.high_impact_events_minutes = []
        assert gate_news_window(ctx).pass_ is True

    def test_nearby_event_denies(self):
        ctx = _clean_context()
        ctx.high_impact_events_minutes = [10]  # 10 min away
        assert gate_news_window(ctx, window_min=30).pass_ is False

    def test_distant_event_passes(self):
        ctx = _clean_context()
        ctx.high_impact_events_minutes = [180]
        assert gate_news_window(ctx, window_min=30).pass_ is True


class TestRegime:
    def test_aligned_long_passes(self):
        ctx = _clean_context()
        assert gate_regime(ctx).pass_ is True

    def test_mismatched_regime_denies_hard(self):
        ctx = _clean_context()
        ctx.side = "short"
        ctx.regime = "trend_up"
        assert gate_regime(ctx).pass_ is False

    def test_chop_yields_low_score_and_fails(self):
        # Scorecard bundle v0.1 (Apr 2026): chop scores 0.3 but the gate
        # threshold lifted from >0.0 to >=0.5 so chop now blocks entries.
        ctx = _clean_context()
        ctx.regime = "chop"
        r = gate_regime(ctx)
        assert r.score > 0.0
        assert r.score < 0.5
        assert r.pass_ is False

    def test_stub_path_still_passes(self):
        # Without a regime classifier the gate is a soft-pass (score 0.5,
        # exactly on the threshold) so it doesn't block unrelated flows.
        ctx = _clean_context()
        ctx.regime = None
        r = gate_regime(ctx)
        assert r.pass_ is True
        assert r.score == 0.5


class TestCorrelation:
    def test_high_corr_passes(self):
        ctx = _clean_context()
        ctx.intermarket_corr = 0.9
        assert gate_correlation(ctx).pass_ is True

    def test_negative_corr_denies(self):
        ctx = _clean_context()
        ctx.intermarket_corr = -0.4
        assert gate_correlation(ctx).pass_ is False


class TestSpread:
    def test_tight_spread_passes(self):
        ctx = _clean_context()
        ctx.spread_ticks = 1.0
        assert gate_spread(ctx).pass_ is True

    def test_wide_spread_denies(self):
        ctx = _clean_context()
        ctx.spread_ticks = 5.0
        assert gate_spread(ctx, max_ticks=2.0).pass_ is False


class TestTimeOfDay:
    def test_green_hour_passes(self):
        ctx = _clean_context()
        ctx.now = datetime(2026, 4, 16, 15, 0, tzinfo=UTC)  # 11:00 ET
        assert gate_time_of_day(ctx).pass_ is True

    def test_red_hour_denies(self):
        ctx = _clean_context()
        ctx.now = datetime(2026, 4, 16, 17, 0, tzinfo=UTC)
        assert gate_time_of_day(ctx).pass_ is False


class TestVolumeConfirm:
    def test_above_sma_passes(self):
        ctx = _clean_context()
        ctx.volumes = [100] * 20 + [200]
        assert gate_volume_confirm(ctx).pass_ is True

    def test_below_sma_denies(self):
        ctx = _clean_context()
        ctx.volumes = [100] * 20 + [30]
        assert gate_volume_confirm(ctx).pass_ is False
