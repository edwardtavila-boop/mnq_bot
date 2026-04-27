"""Tests for mnq.gauntlet.day_aggregate — per-day gauntlet scoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mnq.core.types import Bar
from mnq.gauntlet.day_aggregate import (
    GauntletDayScore,
    _peak_volume_bar_idx,
    blend_deltas,
    gauntlet_day_score,
)


def _make_bar(
    ts: datetime,
    close: float,
    *,
    volume: int = 100,
) -> Bar:
    c = Decimal(str(close))
    return Bar(
        ts=ts,
        open=c,
        high=c + Decimal("1.00"),
        low=c - Decimal("1.00"),
        close=c,
        volume=volume,
        timeframe_sec=60,
    )


def _bar_series(n: int = 40, base_price: float = 20000.0) -> list[Bar]:
    t0 = datetime(2025, 1, 2, 14, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for i in range(n):
        price = base_price + i * 0.25
        bars.append(_make_bar(t0 + timedelta(minutes=i), price, volume=100 + i))
    return bars


class TestPeakVolumeBarIdx:
    def test_finds_max_volume(self) -> None:
        bars = _bar_series(10)
        # Last bar has highest volume (100 + 9 = 109)
        assert _peak_volume_bar_idx(bars) == 9

    def test_empty_returns_zero(self) -> None:
        assert _peak_volume_bar_idx([]) == 0

    def test_spike_in_middle(self) -> None:
        t0 = datetime(2025, 1, 2, 14, 0, tzinfo=UTC)
        bars = [
            _make_bar(t0, 20000.0, volume=50),
            _make_bar(t0 + timedelta(minutes=1), 20000.25, volume=500),
            _make_bar(t0 + timedelta(minutes=2), 20000.50, volume=50),
        ]
        assert _peak_volume_bar_idx(bars) == 1


class TestGauntletDayScore:
    def test_returns_dataclass(self) -> None:
        bars = _bar_series(40)
        score = gauntlet_day_score(bars, regime="trend_up", side="long")
        assert isinstance(score, GauntletDayScore)

    def test_delta_in_range(self) -> None:
        bars = _bar_series(40)
        score = gauntlet_day_score(bars, regime="trend_up")
        assert -1.0 <= score.delta <= 1.0

    def test_voice_in_range(self) -> None:
        bars = _bar_series(40)
        score = gauntlet_day_score(bars, regime="trend_up")
        assert -100.0 <= score.voice <= 100.0

    def test_pass_rate_in_range(self) -> None:
        bars = _bar_series(40)
        score = gauntlet_day_score(bars, regime="trend_up")
        assert 0.0 <= score.pass_rate <= 1.0

    def test_n_passed_n_failed_sum_to_12(self) -> None:
        bars = _bar_series(40)
        score = gauntlet_day_score(bars, regime="trend_up")
        assert score.n_passed + score.n_failed == 12

    def test_empty_bars(self) -> None:
        score = gauntlet_day_score([])
        assert score.delta == 0.0
        assert score.voice == 0.0
        assert score.pass_rate == 0.0

    def test_eval_bar_idx_matches_peak(self) -> None:
        bars = _bar_series(10)
        score = gauntlet_day_score(bars)
        assert score.eval_bar_idx == _peak_volume_bar_idx(bars)

    def test_regime_affects_score(self) -> None:
        bars = _bar_series(40)
        score_up = gauntlet_day_score(bars, regime="trend_up", side="long")
        score_down = gauntlet_day_score(bars, regime="trend_down", side="long")
        # trend_up with long should score better than trend_down with long
        assert score_up.delta >= score_down.delta

    def test_high_vol_blocked(self) -> None:
        bars = _bar_series(40)
        score = gauntlet_day_score(bars, regime="high_vol")
        # high_vol is hard-blocked by regime gate → at least 1 failure
        assert score.n_failed >= 1
        assert "regime" in score.failed_gates


class TestBlendDeltas:
    def test_pure_apex(self) -> None:
        # gauntlet weight = 0 → pure apex
        result = blend_deltas(0.05, 1.0, gauntlet_weight=0.0)
        assert abs(result - 0.05) < 1e-9

    def test_pure_gauntlet(self) -> None:
        # gauntlet weight = 1 → pure gauntlet (scaled)
        result = blend_deltas(0.05, 1.0, gauntlet_weight=1.0)
        # 1.0 * 0.15 = 0.15
        assert abs(result - 0.15) < 1e-9

    def test_default_weight(self) -> None:
        # 85% apex + 15% gauntlet
        result = blend_deltas(0.10, 0.5)
        # 0.85 * 0.10 + 0.15 * (0.5 * 0.15) = 0.085 + 0.01125 = 0.09625
        assert abs(result - 0.09625) < 1e-9

    def test_negative_gauntlet(self) -> None:
        result = blend_deltas(0.05, -1.0, gauntlet_weight=0.15)
        # 0.85 * 0.05 + 0.15 * (-1.0 * 0.15) = 0.0425 - 0.0225 = 0.02
        assert abs(result - 0.02) < 1e-9

    def test_both_negative(self) -> None:
        result = blend_deltas(-0.10, -1.0, gauntlet_weight=0.15)
        # 0.85 * -0.10 + 0.15 * -0.15 = -0.085 - 0.0225 = -0.1075
        assert result < -0.10  # gauntlet makes it worse
