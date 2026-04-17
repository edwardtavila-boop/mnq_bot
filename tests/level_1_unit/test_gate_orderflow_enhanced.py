"""Tests for enhanced gate_orderflow and gate_correlation — Batch 7A/7B."""
from __future__ import annotations

from datetime import UTC, datetime

from mnq.gauntlet.gates.gauntlet12 import (
    GauntletContext,
    gate_correlation,
    gate_orderflow,
)

T0 = datetime(2025, 1, 2, 14, 0, tzinfo=UTC)


def _ctx(**kw) -> GauntletContext:
    defaults = {"now": T0, "side": "long"}
    defaults.update(kw)
    return GauntletContext(**defaults)


# ── gate_orderflow: Tier 1 (full order flow) ──────────────────────────

class TestOrderflowTier1:
    def test_cvd_positive_long_passes(self) -> None:
        v = gate_orderflow(_ctx(cvd=100.0, imbalance=0.3, side="long"))
        assert v.pass_
        assert v.detail["mode"] == "orderflow_tracker"

    def test_cvd_negative_long_fails(self) -> None:
        v = gate_orderflow(_ctx(cvd=-100.0, imbalance=-0.3, side="long"))
        assert not v.pass_

    def test_cvd_negative_short_passes(self) -> None:
        v = gate_orderflow(_ctx(cvd=-100.0, imbalance=-0.3, side="short"))
        assert v.pass_

    def test_cvd_positive_short_fails(self) -> None:
        v = gate_orderflow(_ctx(cvd=100.0, imbalance=0.3, side="short"))
        assert not v.pass_

    def test_absorption_bonus(self) -> None:
        base = gate_orderflow(_ctx(cvd=100.0, imbalance=0.3, absorption_score=0.2))
        bonus = gate_orderflow(_ctx(cvd=100.0, imbalance=0.3, absorption_score=0.8))
        assert bonus.score > base.score

    def test_aggressor_bonus(self) -> None:
        base = gate_orderflow(_ctx(cvd=100.0, imbalance=0.3, buy_aggressor_pct=0.3))
        bonus = gate_orderflow(_ctx(cvd=100.0, imbalance=0.3, buy_aggressor_pct=0.8))
        assert bonus.score > base.score

    def test_max_score_all_aligned(self) -> None:
        v = gate_orderflow(_ctx(
            cvd=200.0, imbalance=0.5, absorption_score=0.9,
            buy_aggressor_pct=0.9, side="long",
        ))
        assert v.score == 1.0

    def test_imbalance_threshold(self) -> None:
        """Imbalance below 0.1 doesn't count."""
        weak = gate_orderflow(_ctx(cvd=100.0, imbalance=0.05, side="long"))
        strong = gate_orderflow(_ctx(cvd=100.0, imbalance=0.3, side="long"))
        assert strong.score > weak.score


# ── gate_orderflow: Tier 2 (CVD only) ─────────────────────────────────

class TestOrderflowTier2:
    def test_cvd_only_positive_long(self) -> None:
        v = gate_orderflow(_ctx(cvd=50.0, side="long"))
        assert v.pass_
        assert v.detail["mode"] == "cvd_only"

    def test_cvd_only_negative_long(self) -> None:
        v = gate_orderflow(_ctx(cvd=-50.0, side="long"))
        assert not v.pass_
        assert v.score == 0.2


# ── gate_orderflow: Tier 3 (proxy fallback) ───────────────────────────

class TestOrderflowTier3:
    def test_proxy_with_sufficient_bars(self) -> None:
        closes = [20000 + i for i in range(10)]
        volumes = [100] * 10
        v = gate_orderflow(_ctx(closes=closes, volumes=volumes, side="long"))
        assert v.detail["mode"] == "proxy"

    def test_proxy_insufficient_bars(self) -> None:
        v = gate_orderflow(_ctx(closes=[20000], volumes=[100], side="long"))
        assert v.pass_  # stub passes
        assert v.detail["mode"] == "stub"


# ── gate_correlation: Mode 2 (computed) ────────────────────────────────

class TestCorrelationComputed:
    def test_perfectly_correlated(self) -> None:
        mnq = [20000 + i for i in range(20)]
        es = [4500 + i * 0.5 for i in range(20)]  # same direction
        v = gate_correlation(_ctx(closes=mnq, es_closes=es))
        assert v.pass_
        assert v.detail["mode"] == "computed"
        assert v.detail["corr"] > 0.9

    def test_inversely_correlated(self) -> None:
        mnq = [20000 + i for i in range(20)]
        es = [4500 - i * 0.5 for i in range(20)]  # opposite direction
        v = gate_correlation(_ctx(closes=mnq, es_closes=es))
        assert not v.pass_
        assert v.detail["corr"] < 0

    def test_insufficient_es_bars_stub(self) -> None:
        mnq = [20000 + i for i in range(20)]
        es = [4500, 4501]  # too few
        v = gate_correlation(_ctx(closes=mnq, es_closes=es))
        assert v.pass_  # stub passes
        assert v.detail["mode"] == "stub"

    def test_precomputed_takes_precedence(self) -> None:
        """If intermarket_corr is set, use it even if es_closes exist."""
        mnq = [20000 + i for i in range(20)]
        es = [4500 + i for i in range(20)]
        v = gate_correlation(_ctx(closes=mnq, es_closes=es, intermarket_corr=0.8))
        assert v.detail["mode"] == "precomputed"

    def test_flat_series_zero_corr(self) -> None:
        mnq = [20000.0] * 20
        es = [4500.0] * 20
        v = gate_correlation(_ctx(closes=mnq, es_closes=es))
        assert v.detail["corr"] == 0.0


# ── bridge populates orderflow fields ──────────────────────────────────

class TestBridgeOrderflowIntegration:
    def test_context_from_bars_has_cvd(self) -> None:
        from datetime import timedelta
        from decimal import Decimal

        from mnq.core.types import Bar
        from mnq.gauntlet.bridge import context_from_bars

        bars = [
            Bar(
                ts=T0 + timedelta(minutes=i),
                open=Decimal("20000"),
                high=Decimal("20002"),
                low=Decimal("19998"),
                close=Decimal("20002"),
                volume=100,
                timeframe_sec=60,
            )
            for i in range(10)
        ]
        ctx = context_from_bars(bars, bar_idx=9, side="long")
        assert ctx.cvd is not None
        assert ctx.imbalance is not None
        assert ctx.absorption_score is not None
        assert ctx.buy_aggressor_pct is not None

    def test_context_from_bars_es_closes(self) -> None:
        from datetime import timedelta
        from decimal import Decimal

        from mnq.core.types import Bar
        from mnq.gauntlet.bridge import context_from_bars

        bars = [
            Bar(
                ts=T0 + timedelta(minutes=i),
                open=Decimal("20000"),
                high=Decimal("20002"),
                low=Decimal("19998"),
                close=Decimal("20001"),
                volume=100,
                timeframe_sec=60,
            )
            for i in range(10)
        ]
        es = [4500 + i for i in range(10)]
        ctx = context_from_bars(bars, bar_idx=9, side="long", es_closes=es)
        assert ctx.es_closes == es
