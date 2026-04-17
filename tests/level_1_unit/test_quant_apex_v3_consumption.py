"""Quant agent fold-in of eta_v3_voices into probability estimate.

The adapter tested in ``test_eta_v3_adapter.py`` only shapes the dict.
This test locks in the *consumption* side: given a payload that already
carries ``eta_v3_voices``, the Quant agent's ``evaluate()`` blends the
15-voice agreement into its probability while never overriding a spec
violation (KILL/MODIFY stays KILL/MODIFY regardless of Apex opinion).

These tests import the real ``firm.agents.core.QuantAgent`` through the
same path-injection pattern the bridge shim uses. When the_firm_complete
isn't on disk (e.g., a clean CI clone), the whole module skips — the
Apex V3 batch is explicitly fail-open on both ends.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _firm_package_parent() -> Path | None:
    """Locate the_firm_complete/desktop_app across common layouts.

    Honors ``FIRM_CODE_PATH`` env var first, then falls back to the
    user's OneDrive canonical location documented in CLAUDE.md.
    """
    env = os.environ.get("FIRM_CODE_PATH")
    if env:
        p = Path(env)
        if p.name == "firm":
            p = p.parent
        if (p / "firm" / "agents" / "core.py").exists():
            return p
    for candidate in (
        Path.home() / "OneDrive" / "the_firm_complete" / "desktop_app",
        Path("C:/Users/edwar/OneDrive/The_Firm/the_firm_complete/desktop_app"),
        Path("/sessions/kind-keen-faraday/mnt/OneDrive/the_firm_complete/desktop_app"),
    ):
        if (candidate / "firm" / "agents" / "core.py").exists():
            return candidate
    return None


_PARENT = _firm_package_parent()
if _PARENT is None:
    pytest.skip(
        "the_firm_complete package not on disk — Quant fold-in test requires it",
        allow_module_level=True,
    )
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

# These imports cannot happen before the sys.path insert above, so
# module-level import ordering is intentional.
from firm.agents.base import AgentInput  # noqa: E402
from firm.agents.core import QuantAgent  # noqa: E402
from firm.types import Verdict  # noqa: E402

# ----- fixtures --------------------------------------------------------------

def _clean_spec(**overrides) -> dict:
    """A spec that passes every rule-based gate in QuantAgent."""
    base = {
        "sample_size": 500,
        "expected_expectancy_r": 0.5,
        "oos_degradation_pct": 10,
        "entry_logic": "ORB break",
        "stop_logic": "1.5 ATR",
        "target_logic": "2R",
        "dd_kill_switch_r": -10,
        "side": "long",
    }
    base.update(overrides)
    return base


def _apex_voices(**overrides) -> dict:
    """eta_v3_voices dict as produced by the adapter's ApexVoiceSnapshot."""
    base = {
        "regime": "TREND",
        "pm_final": 3.4,
        "quant_total": 6.0,
        "red_team": 1.5,
        "red_team_weighted": 1.5,
        "voice_agree": 11,
        "direction": 1,
        "fire_long": True,
        "fire_short": False,
        "setup_name": "ORB_RETEST_LONG",
        "blocked_reason": "",
        "voices": {f"V{i}": 0.5 for i in range(1, 16)},
        "source": "eta_v3",
    }
    base.update(overrides)
    return base


def _build_input(spec: dict, voices: dict | None) -> AgentInput:
    payload: dict = {"spec": spec}
    if voices is not None:
        payload["eta_v3_voices"] = voices
    return AgentInput(
        strategy_id="test_strategy",
        decision_context="unit_test",
        payload=payload,
    )


# ----- no-op / backwards-compat ---------------------------------------------

class TestNoApexVoices:
    def test_no_apex_key_is_exact_noop_on_probability(self):
        agent = QuantAgent()
        spec = _clean_spec(expected_expectancy_r=0.5)
        out = agent.evaluate(_build_input(spec, voices=None))
        # Base rule: expectancy >= 0.4 → probability = 0.7
        assert out.probability == pytest.approx(0.7)
        assert out.verdict == Verdict.GO

    def test_no_apex_key_payload_carries_consumed_false(self):
        agent = QuantAgent()
        out = agent.evaluate(_build_input(_clean_spec(), voices=None))
        assert out.payload["eta_v3"]["consumed"] is False
        assert out.payload["eta_v3"]["delta"] == 0.0

    def test_non_dict_apex_voices_is_ignored(self):
        agent = QuantAgent()
        out = agent.evaluate(
            AgentInput(
                strategy_id="s",
                decision_context="c",
                payload={"spec": _clean_spec(), "eta_v3_voices": "not-a-dict"},
            )
        )
        assert out.payload["eta_v3"]["consumed"] is False
        assert out.probability == pytest.approx(0.7)

    def test_violations_still_dominate_with_apex_present(self):
        """Spec MODIFY must survive even a fully-corroborating Apex signal."""
        agent = QuantAgent()
        bad_spec = _clean_spec(sample_size=0, expected_expectancy_r=0.0)
        voices = _apex_voices(voice_agree=15, fire_long=True, direction=1)
        out = agent.evaluate(_build_input(bad_spec, voices))
        assert out.verdict == Verdict.MODIFY
        # Base probability is 0.2; 0.75*0.2 + 0.25*1.0 = 0.4 blended, no penalty
        # → adjusted 0.4, delta +0.2
        assert out.payload["eta_v3"]["consumed"] is True
        assert out.payload["eta_v3"]["base_probability"] == pytest.approx(0.2)
        assert out.probability == pytest.approx(0.4)


# ----- fold-in math ---------------------------------------------------------

class TestApexFoldMath:
    def test_full_agreement_lifts_probability(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(
                _clean_spec(expected_expectancy_r=0.5),
                _apex_voices(voice_agree=15, direction=1, fire_long=True),
            )
        )
        # base=0.7, signal=1.0, w=0.25 → 0.75*0.7 + 0.25*1.0 = 0.775
        assert out.probability == pytest.approx(0.775)
        assert out.payload["eta_v3"]["strong_corroboration"] is True
        assert out.payload["eta_v3"]["supporting"] is True
        assert out.payload["eta_v3"]["delta"] == pytest.approx(0.075)

    def test_zero_agreement_drags_probability_down(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(
                _clean_spec(expected_expectancy_r=0.5),
                _apex_voices(voice_agree=0, direction=0,
                             fire_long=False, fire_short=False),
            )
        )
        # base=0.7, signal=0.0, w=0.25 → 0.75*0.7 + 0.25*0.0 = 0.525
        assert out.probability == pytest.approx(0.525)
        assert out.payload["eta_v3"]["supporting"] is False
        assert out.payload["eta_v3"]["strong_corroboration"] is False
        assert out.payload["eta_v3"]["delta"] == pytest.approx(-0.175)

    def test_blocked_reason_applies_penalty(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(
                _clean_spec(expected_expectancy_r=0.5),
                _apex_voices(voice_agree=11, direction=1, fire_long=True,
                             blocked_reason="vol_regime_veto"),
            )
        )
        # base=0.7, signal=11/15≈0.7333, blended = 0.75*0.7 + 0.25*0.7333 = 0.7083
        # penalty=0.10 → adjusted = 0.6083
        assert out.probability == pytest.approx(0.6083, abs=1e-3)
        assert out.payload["eta_v3"]["penalty_applied"] == pytest.approx(0.10)
        assert out.payload["eta_v3"]["blocked_reason"] == "vol_regime_veto"

    def test_direction_disagreement_applies_penalty(self):
        agent = QuantAgent()
        # Spec is long, apex says short
        out = agent.evaluate(
            _build_input(
                _clean_spec(side="long", expected_expectancy_r=0.5),
                _apex_voices(voice_agree=12, direction=-1,
                             fire_long=False, fire_short=True),
            )
        )
        # base=0.7, signal=12/15=0.8 → blended 0.75*0.7 + 0.25*0.8 = 0.725
        # direction penalty=0.05 → adjusted 0.675
        assert out.probability == pytest.approx(0.675)
        assert out.payload["eta_v3"]["penalty_applied"] == pytest.approx(0.05)

    def test_both_penalties_stack(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(
                _clean_spec(side="long", expected_expectancy_r=0.5),
                _apex_voices(voice_agree=6, direction=-1,
                             fire_long=False, fire_short=False,
                             blocked_reason="regime_veto"),
            )
        )
        # base=0.7, signal=6/15=0.4 → blended 0.75*0.7 + 0.25*0.4 = 0.625
        # both penalties: 0.10 + 0.05 = 0.15 → adjusted 0.475
        assert out.probability == pytest.approx(0.475)
        assert out.payload["eta_v3"]["penalty_applied"] == pytest.approx(0.15)

    def test_adjusted_probability_clipped_to_zero(self):
        agent = QuantAgent()
        # MODIFY base=0.2, plus stacked penalties would go below zero
        bad_spec = _clean_spec(side="long", sample_size=0, expected_expectancy_r=0.0)
        out = agent.evaluate(
            _build_input(
                bad_spec,
                _apex_voices(voice_agree=0, direction=-1,
                             fire_long=False, fire_short=False,
                             blocked_reason="regime_veto"),
            )
        )
        # base=0.2, signal=0.0 → blended 0.15, penalty=0.15 → 0.0 (clip)
        assert out.probability == 0.0
        assert out.payload["eta_v3"]["adjusted_probability"] == 0.0

    def test_adjusted_probability_clipped_to_one(self):
        agent = QuantAgent()
        # Stress test: patch blend weight to 1.0 and expectancy high
        # so blended = apex_signal. With 15/15 + base 0.7 we get 1.0.
        original = QuantAgent.APEX_V3_BLEND_WEIGHT
        try:
            QuantAgent.APEX_V3_BLEND_WEIGHT = 1.0
            out = agent.evaluate(
                _build_input(
                    _clean_spec(expected_expectancy_r=0.5),
                    _apex_voices(voice_agree=15, direction=1, fire_long=True),
                )
            )
        finally:
            QuantAgent.APEX_V3_BLEND_WEIGHT = original
        assert out.probability == 1.0

    def test_summary_direction_label_map(self):
        agent = QuantAgent()
        out_long = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(direction=1))
        )
        out_flat = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(direction=0, fire_long=False))
        )
        out_short = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(direction=-1, fire_long=False,
                                                      fire_short=True))
        )
        assert out_long.payload["eta_v3"]["direction_label"] == "LONG"
        assert out_flat.payload["eta_v3"]["direction_label"] == "FLAT"
        assert out_short.payload["eta_v3"]["direction_label"] == "SHORT"


# ----- reasoning + drivers ---------------------------------------------------

class TestApexReasoningTrail:
    def test_reasoning_suffix_includes_voice_count(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(_clean_spec(),
                         _apex_voices(voice_agree=11, regime="TREND"))
        )
        assert "Apex V3 corroboration" in out.reasoning
        assert "11/15" in out.reasoning
        assert "TREND" in out.reasoning

    def test_reasoning_unchanged_without_voices(self):
        agent = QuantAgent()
        out = agent.evaluate(_build_input(_clean_spec(), voices=None))
        assert "Apex V3" not in out.reasoning

    def test_tertiary_driver_reflects_apex_when_consumed(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(voice_agree=13))
        )
        assert out.tertiary_driver.startswith("eta_v3")
        assert "13/15" in out.tertiary_driver

    def test_tertiary_driver_default_without_voices(self):
        agent = QuantAgent()
        out = agent.evaluate(_build_input(_clean_spec(), voices=None))
        assert "Spec completeness" in out.tertiary_driver


# ----- malformed input tolerance --------------------------------------------

class TestMalformedApexVoices:
    def test_string_voice_agree_treated_as_zero(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(voice_agree="not-an-int"))
        )
        assert out.payload["eta_v3"]["voice_agree"] == 0

    def test_out_of_range_voice_agree_clamped(self):
        agent = QuantAgent()
        out_high = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(voice_agree=99))
        )
        out_low = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(voice_agree=-5))
        )
        assert out_high.payload["eta_v3"]["voice_agree"] == 15
        assert out_low.payload["eta_v3"]["voice_agree"] == 0

    def test_non_int_direction_defaults_to_zero(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(direction="who-knows"))
        )
        assert out.payload["eta_v3"]["direction"] == 0

    def test_none_blocked_reason_is_empty_string(self):
        agent = QuantAgent()
        out = agent.evaluate(
            _build_input(_clean_spec(), _apex_voices(blocked_reason=None))
        )
        assert out.payload["eta_v3"]["blocked_reason"] == ""
        assert out.payload["eta_v3"]["penalty_applied"] == 0.0
