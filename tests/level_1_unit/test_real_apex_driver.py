"""Level-1 unit tests for scripts/real_eta_driver.py (Batch 3F)."""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
APEX_PY = REPO_ROOT / "eta_v3_framework" / "python"
for p in (str(SCRIPTS), str(APEX_PY)):
    if p not in sys.path:
        sys.path.insert(0, p)

from real_eta_driver import (  # noqa: E402
    _aggregate_day,
    _to_apex_bar,
    day_pm_output_from_real_apex,
    run_day_through_apex,
)

from mnq.eta_v3.gate import apex_gate  # noqa: E402
from mnq.core.types import Bar as MnqBar  # noqa: E402


def _make_bars(n: int, *, start_px: float = 18000.0, slope: float = 0.5) -> list[MnqBar]:
    """Deterministic trending-up synthetic bars for the driver tests."""
    t0 = datetime(2026, 4, 15, 13, 30, tzinfo=UTC)
    out: list[MnqBar] = []
    for i in range(n):
        px = start_px + slope * i
        out.append(MnqBar(
            ts=t0 + timedelta(minutes=i),
            open=Decimal(f"{px:.2f}"),
            high=Decimal(f"{px + 1.0:.2f}"),
            low=Decimal(f"{px - 0.25:.2f}"),
            close=Decimal(f"{px + 0.75:.2f}"),
            volume=1000 + i,
            timeframe_sec=60,
        ))
    return out


class TestToApexBar:
    def test_price_types_converted_to_float(self):
        mb = _make_bars(1)[0]
        ab = _to_apex_bar(mb)
        for attr in ("open", "high", "low", "close", "volume"):
            assert isinstance(getattr(ab, attr), float)

    def test_time_is_unix_seconds(self):
        mb = _make_bars(1)[0]
        ab = _to_apex_bar(mb)
        assert isinstance(ab.time, int)
        assert ab.time == int(mb.ts.timestamp())

    def test_ohlc_values_preserved(self):
        mb = _make_bars(1)[0]
        ab = _to_apex_bar(mb)
        assert ab.open == pytest.approx(float(mb.open))
        assert ab.close == pytest.approx(float(mb.close))
        assert ab.volume == pytest.approx(float(mb.volume))


class TestRunDayThroughApex:
    def test_produces_one_decision_per_bar(self):
        bars = _make_bars(50)
        decisions = run_day_through_apex(bars)
        assert len(decisions) == 50

    def test_empty_bars_returns_empty(self):
        assert run_day_through_apex([]) == []

    def test_decisions_have_voice_scores(self):
        bars = _make_bars(30)
        decisions = run_day_through_apex(bars)
        # Every decision should have a voice_agree in [0, 15]
        for d in decisions:
            assert 0 <= d.voice_agree <= 15
            # pm_final is signed; just make sure it's a real number
            assert d.pm_final == d.pm_final  # not NaN


class TestAggregateDay:
    def test_empty_returns_zeros(self):
        out = _aggregate_day([])
        assert out == {
            "voice_agree": 0,
            "pm_final": 0.0,
            "direction": 0,
            "engine_live": False,
            "fire_count": 0,
            "setup_names": [],
        }

    def test_voice_agree_takes_max(self):
        bars = _make_bars(20)
        decisions = run_day_through_apex(bars)
        agg = _aggregate_day(decisions)
        assert agg["voice_agree"] == max(d.voice_agree for d in decisions)

    def test_engine_live_reflects_any_fire(self):
        bars = _make_bars(20)
        decisions = run_day_through_apex(bars)
        agg = _aggregate_day(decisions)
        any_fire = any(d.fire_long or d.fire_short for d in decisions)
        assert agg["engine_live"] is any_fire


class TestDayPmOutputShape:
    """Contract: apex_gate must accept our output dict unchanged."""

    def test_required_keys_present(self):
        bars = _make_bars(40)
        out = day_pm_output_from_real_apex(bars)
        assert out["verdict"] == "GO"
        assert "probability" in out
        apex = out["payload"]["eta_v3"]
        for k in (
            "consumed", "voice_agree", "pm_final", "engine_live",
            "base_probability", "adjusted_probability", "delta",
        ):
            assert k in apex, f"missing key: {k}"
        assert apex["consumed"] is True
        assert apex["source"] == "real_engine"

    def test_delta_is_finite(self):
        bars = _make_bars(40)
        out = day_pm_output_from_real_apex(bars)
        delta = out["payload"]["eta_v3"]["delta"]
        assert isinstance(delta, float)
        # Must be in [-1, 1]; probability is bounded [0, 1]
        assert -1.0 <= delta <= 1.0

    def test_empty_bars_produces_safe_default(self):
        out = day_pm_output_from_real_apex([])
        apex = out["payload"]["eta_v3"]
        assert apex["voice_agree"] == 0
        assert apex["engine_live"] is False
        assert apex["fire_count"] == 0
        # Gate must still accept it
        gate_out = apex_gate(out)
        assert gate_out["action"] in ("full", "reduced", "skip")

    def test_gate_accepts_output(self):
        bars = _make_bars(60)
        out = day_pm_output_from_real_apex(bars)
        gate_out = apex_gate(out)
        # Must produce a valid decision
        assert gate_out["action"] in ("full", "reduced", "skip")
        assert gate_out["size_mult"] in (0.0, 0.5, 1.0)
        assert "reason" in gate_out


class TestDeterminism:
    """Same bars in → same aggregated snapshot out."""

    def test_two_runs_same_input_same_output(self):
        bars = _make_bars(50)
        out1 = day_pm_output_from_real_apex(bars)
        out2 = day_pm_output_from_real_apex(bars)
        a1 = out1["payload"]["eta_v3"]
        a2 = out2["payload"]["eta_v3"]
        assert a1["voice_agree"] == a2["voice_agree"]
        assert a1["pm_final"] == a2["pm_final"]
        assert a1["delta"] == a2["delta"]
        assert a1["fire_count"] == a2["fire_count"]
