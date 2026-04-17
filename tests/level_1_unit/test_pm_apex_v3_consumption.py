"""PM agent fold-in of eta_v3_voices + eta_v3_pm_final into decision.

Batch 3B: the PM stage now blends the Apex V3 15-voice signal into its
final probability while *never* flipping verdicts. KILL stays KILL,
HOLD stays HOLD, GO stays GO — only the probability shifts.

Mirror of ``test_quant_eta_v3_consumption.py`` but at the decision
stage. Since PM requires five prior stage outputs + a Red Team dissent
entry, these tests fabricate minimal AgentOutput objects to feed in.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _firm_package_parent() -> Path | None:
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
        "the_firm_complete package not on disk — PM fold-in test requires it",
        allow_module_level=True,
    )
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from firm.agents.base import AgentInput, AgentOutput  # noqa: E402
from firm.agents.core import PMAgent  # noqa: E402
from firm.types import Verdict  # noqa: E402

# ----- fixtures --------------------------------------------------------------

def _mk(agent_name: str, verdict: Verdict, *, payload: dict | None = None,
        probability: float = 0.7) -> AgentOutput:
    return AgentOutput(
        agent_name=agent_name,
        verdict=verdict,
        probability=probability,
        confidence_interval=(max(0.0, probability - 0.1),
                             min(1.0, probability + 0.1)),
        time_horizon="immediate",
        falsification_criteria="n/a",
        reasoning=f"{agent_name} {verdict.value}",
        primary_driver=f"{agent_name}_primary",
        payload=payload or {},
    )


def _clean_agent_outputs() -> dict:
    """All 5 prior stages GO, red_team carries one attack so PM can proceed."""
    return {
        "quant": _mk("quant", Verdict.GO, payload={"expectancy_r": 0.5}),
        "red_team": _mk("red_team", Verdict.GO,
                        payload={"attacks": [{"surface": "slippage",
                                              "survived": True}]}),
        "risk": _mk("risk", Verdict.GO,
                    payload={"per_trade_risk_pct": 0.0025, "dd_kill_r": -10}),
        "macro": _mk("macro", Verdict.GO),
        "micro": _mk("micro", Verdict.GO),
    }


def _apex_voices(**overrides) -> dict:
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


def _build_input(agent_outputs: dict, *, voices: dict | None = None,
                 pm_final: float | None = None,
                 side: str = "long") -> AgentInput:
    payload: dict = {
        "agent_outputs": agent_outputs,
        "spec": {"side": side},
        "side": side,
    }
    if voices is not None:
        payload["eta_v3_voices"] = voices
    if pm_final is not None:
        payload["eta_v3_pm_final"] = pm_final
    return AgentInput(
        strategy_id="test_strategy",
        decision_context="unit_test",
        payload=payload,
    )


# ----- no-op / backwards-compat ---------------------------------------------

class TestNoApexVoices:
    def test_clean_go_without_voices_unchanged(self):
        agent = PMAgent()
        out = agent.evaluate(_build_input(_clean_agent_outputs(), voices=None))
        assert out.verdict == Verdict.GO
        assert out.probability == pytest.approx(0.6)
        assert out.payload["eta_v3"]["consumed"] is False
        assert out.payload["eta_v3"]["delta"] == 0.0

    def test_no_apex_key_reasoning_has_no_apex_suffix(self):
        agent = PMAgent()
        out = agent.evaluate(_build_input(_clean_agent_outputs(), voices=None))
        assert "Apex V3" not in out.reasoning

    def test_non_dict_apex_voices_is_ignored(self):
        agent = PMAgent()
        ao = _clean_agent_outputs()
        in_ = AgentInput(
            strategy_id="s",
            decision_context="c",
            payload={
                "agent_outputs": ao,
                "spec": {"side": "long"},
                "side": "long",
                "eta_v3_voices": "not-a-dict",
            },
        )
        out = agent.evaluate(in_)
        assert out.payload["eta_v3"]["consumed"] is False
        assert out.probability == pytest.approx(0.6)


# ----- verdict preservation -------------------------------------------------

class TestVerdictsNeverFlip:
    def test_kill_stays_kill_even_with_full_apex_agreement(self):
        agent = PMAgent()
        ao = _clean_agent_outputs()
        ao["macro"] = _mk("macro", Verdict.KILL)  # one KILL wins
        voices = _apex_voices(voice_agree=15, direction=1, fire_long=True,
                              pm_final=50.0)
        out = agent.evaluate(_build_input(ao, voices=voices, pm_final=50.0))
        assert out.verdict == Verdict.KILL
        assert out.payload["eta_v3"]["consumed"] is True

    def test_hold_stays_hold_with_apex_corroboration(self):
        agent = PMAgent()
        ao = _clean_agent_outputs()
        ao["risk"] = _mk("risk", Verdict.HOLD,
                         payload={"per_trade_risk_pct": 0.0025})
        voices = _apex_voices(voice_agree=14, direction=1)
        out = agent.evaluate(_build_input(ao, voices=voices, pm_final=45.0))
        assert out.verdict == Verdict.HOLD
        assert out.payload["eta_v3"]["consumed"] is True

    def test_modify_stays_modify_with_apex_disagreement(self):
        agent = PMAgent()
        ao = _clean_agent_outputs()
        ao["quant"] = _mk("quant", Verdict.MODIFY)
        ao["risk"] = _mk("risk", Verdict.MODIFY,
                         payload={"per_trade_risk_pct": 0.0025})
        voices = _apex_voices(voice_agree=0, direction=-1,
                              fire_long=False, fire_short=True)
        out = agent.evaluate(_build_input(ao, voices=voices, pm_final=-2.0))
        assert out.verdict == Verdict.MODIFY


# ----- fold-in math (GO path) -----------------------------------------------

class TestApexFoldMath:
    def test_full_agreement_lifts_go_probability_with_bonus(self):
        agent = PMAgent()
        voices = _apex_voices(voice_agree=15, direction=1, fire_long=True)
        out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                          voices=voices, pm_final=50.0))
        # base=0.6, signal=1.0, w=0.20 → blend = 0.8*0.6 + 0.2*1.0 = 0.68
        # strong + engine_live (50>=40) → +0.05 bonus → 0.73
        assert out.probability == pytest.approx(0.73, abs=1e-3)
        assert out.payload["eta_v3"]["bonus_applied"] == pytest.approx(0.05)
        assert out.payload["eta_v3"]["strong_corroboration"] is True
        assert out.payload["eta_v3"]["engine_live"] is True
        assert out.payload["eta_v3"]["verdict_alignment_label"] == "MATCH"

    def test_zero_agreement_drags_go_probability_down(self):
        agent = PMAgent()
        voices = _apex_voices(voice_agree=0, direction=0,
                              fire_long=False, fire_short=False)
        out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                          voices=voices, pm_final=0.0))
        # base=0.6, signal=0.0 → blend = 0.8*0.6 + 0.2*0.0 = 0.48
        # no bonus (not strong) → adjusted 0.48
        assert out.probability == pytest.approx(0.48, abs=1e-3)
        assert out.payload["eta_v3"]["bonus_applied"] == 0.0

    def test_direction_conflict_applies_penalty_on_go(self):
        agent = PMAgent()
        # Spec side long, apex direction short AND strong, engine live
        voices = _apex_voices(voice_agree=13, direction=-1,
                              fire_long=False, fire_short=True)
        out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                          voices=voices, pm_final=50.0,
                                          side="long"))
        # base=0.6, signal=13/15≈0.8667 → blend = 0.8*0.6 + 0.2*0.8667 = 0.6533
        # strong + engine_live → +0.05 bonus
        # strong + direction conflict → -0.05 penalty
        # net change vs blend = 0 → 0.6533
        assert out.probability == pytest.approx(0.6533, abs=1e-3)
        assert out.payload["eta_v3"]["bonus_applied"] == pytest.approx(0.05)
        assert out.payload["eta_v3"]["penalty_applied"] == pytest.approx(0.05)
        assert out.payload["eta_v3"]["verdict_alignment_label"] == "CONFLICT"

    def test_engine_below_gate_no_bonus(self):
        agent = PMAgent()
        # pm_final=10 below PM_GATE (40.0) → engine_live=False → no bonus
        voices = _apex_voices(voice_agree=15, direction=1)
        out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                          voices=voices, pm_final=10.0))
        # engine_live=False → no bonus
        # base=0.6, signal=1.0 → blend 0.68, no bonus → 0.68
        assert out.payload["eta_v3"]["engine_live"] is False
        assert out.payload["eta_v3"]["bonus_applied"] == 0.0
        assert out.probability == pytest.approx(0.68, abs=1e-3)

    def test_adjusted_probability_clipped_to_one(self):
        agent = PMAgent()
        voices = _apex_voices(voice_agree=15, direction=1)
        original_w = PMAgent.APEX_V3_PM_WEIGHT
        original_b = PMAgent.APEX_V3_AGREE_BOOST
        try:
            # Force blend to hit 1.0 before clipping
            PMAgent.APEX_V3_PM_WEIGHT = 1.0
            PMAgent.APEX_V3_AGREE_BOOST = 0.5
            out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                              voices=voices, pm_final=100.0))
        finally:
            PMAgent.APEX_V3_PM_WEIGHT = original_w
            PMAgent.APEX_V3_AGREE_BOOST = original_b
        assert out.probability == 1.0

    def test_adjusted_probability_clipped_to_zero(self):
        agent = PMAgent()
        voices = _apex_voices(voice_agree=0, direction=-1,
                              fire_long=False, fire_short=True)
        original_w = PMAgent.APEX_V3_PM_WEIGHT
        original_p = PMAgent.APEX_V3_DISAGREE_PENALTY
        try:
            # Force blend + penalty to cross zero
            PMAgent.APEX_V3_PM_WEIGHT = 1.0
            PMAgent.APEX_V3_DISAGREE_PENALTY = 0.5
            # Need strong agreement for penalty → but voice_agree=0 not strong
            # So zero out base and test min clip via 0 base + 0 signal
            out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                              voices=_apex_voices(voice_agree=0,
                                                                  direction=0,
                                                                  fire_long=False,
                                                                  fire_short=False),
                                              pm_final=0.0, side="long"))
        finally:
            PMAgent.APEX_V3_PM_WEIGHT = original_w
            PMAgent.APEX_V3_DISAGREE_PENALTY = original_p
        # base=0.6, w=1.0, signal=0.0 → blend 0.0, no penalty (not strong) → 0.0
        assert out.probability == 0.0

    def test_summary_direction_label_map(self):
        agent = PMAgent()
        out_long = agent.evaluate(_build_input(_clean_agent_outputs(),
                                               voices=_apex_voices(direction=1)))
        out_flat = agent.evaluate(_build_input(_clean_agent_outputs(),
                                               voices=_apex_voices(direction=0,
                                                                   fire_long=False)))
        out_short = agent.evaluate(_build_input(_clean_agent_outputs(),
                                                voices=_apex_voices(direction=-1,
                                                                    fire_long=False,
                                                                    fire_short=True)))
        assert out_long.payload["eta_v3"]["direction_label"] == "LONG"
        assert out_flat.payload["eta_v3"]["direction_label"] == "FLAT"
        assert out_short.payload["eta_v3"]["direction_label"] == "SHORT"

    def test_kill_base_probability_blended_but_no_bonus(self):
        """KILL base prob 0.9 gets blended with voices but no direction bonus."""
        agent = PMAgent()
        ao = _clean_agent_outputs()
        ao["quant"] = _mk("quant", Verdict.KILL)
        voices = _apex_voices(voice_agree=15, direction=1)
        out = agent.evaluate(_build_input(ao, voices=voices, pm_final=50.0))
        # base=0.9, signal=1.0 → blend = 0.8*0.9 + 0.2*1.0 = 0.92
        # go_like=False → no bonus, no penalty
        assert out.verdict == Verdict.KILL
        assert out.probability == pytest.approx(0.92, abs=1e-3)
        assert out.payload["eta_v3"]["bonus_applied"] == 0.0
        assert out.payload["eta_v3"]["penalty_applied"] == 0.0


# ----- reasoning trail ------------------------------------------------------

class TestApexReasoningTrail:
    def test_reasoning_suffix_includes_voice_count_and_pm_final(self):
        agent = PMAgent()
        voices = _apex_voices(voice_agree=11, pm_final=45.0)
        out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                          voices=voices, pm_final=45.0))
        assert "Apex V3 PM corroboration" in out.reasoning
        assert "11/15" in out.reasoning
        assert "pm_final=" in out.reasoning

    def test_reasoning_unchanged_without_voices(self):
        agent = PMAgent()
        out = agent.evaluate(_build_input(_clean_agent_outputs(), voices=None))
        assert "Apex V3" not in out.reasoning

    def test_reasoning_includes_alignment_label(self):
        agent = PMAgent()
        voices = _apex_voices(voice_agree=13, direction=1)
        out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                          voices=voices, pm_final=45.0,
                                          side="long"))
        assert "align=MATCH" in out.reasoning

    def test_reasoning_includes_engine_status(self):
        agent = PMAgent()
        # Above gate
        voices = _apex_voices(voice_agree=11)
        out_live = agent.evaluate(_build_input(_clean_agent_outputs(),
                                               voices=voices, pm_final=45.0))
        assert "(live)" in out_live.reasoning
        # Below gate
        out_dead = agent.evaluate(_build_input(_clean_agent_outputs(),
                                               voices=voices, pm_final=10.0))
        assert "(below gate)" in out_dead.reasoning


# ----- malformed input tolerance --------------------------------------------

class TestMalformedApexVoices:
    def test_string_voice_agree_treated_as_zero(self):
        agent = PMAgent()
        voices = _apex_voices(voice_agree="not-an-int")
        out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                          voices=voices, pm_final=5.0))
        assert out.payload["eta_v3"]["voice_agree"] == 0

    def test_out_of_range_voice_agree_clamped(self):
        agent = PMAgent()
        out_high = agent.evaluate(_build_input(_clean_agent_outputs(),
                                               voices=_apex_voices(voice_agree=99),
                                               pm_final=5.0))
        out_low = agent.evaluate(_build_input(_clean_agent_outputs(),
                                              voices=_apex_voices(voice_agree=-5),
                                              pm_final=5.0))
        assert out_high.payload["eta_v3"]["voice_agree"] == 15
        assert out_low.payload["eta_v3"]["voice_agree"] == 0

    def test_non_int_direction_defaults_to_zero(self):
        agent = PMAgent()
        voices = _apex_voices(direction="???")
        out = agent.evaluate(_build_input(_clean_agent_outputs(),
                                          voices=voices, pm_final=5.0))
        assert out.payload["eta_v3"]["direction"] == 0

    def test_missing_pm_final_falls_back_to_voices_key(self):
        agent = PMAgent()
        voices = _apex_voices(voice_agree=11, pm_final=50.0)
        # No top-level eta_v3_pm_final — must fall back to voices["pm_final"]
        in_ = AgentInput(
            strategy_id="s",
            decision_context="c",
            payload={
                "agent_outputs": _clean_agent_outputs(),
                "spec": {"side": "long"},
                "side": "long",
                "eta_v3_voices": voices,
            },
        )
        out = agent.evaluate(in_)
        assert out.payload["eta_v3"]["pm_final"] == pytest.approx(50.0)
        assert out.payload["eta_v3"]["engine_live"] is True
