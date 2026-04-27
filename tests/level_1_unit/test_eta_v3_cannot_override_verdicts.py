"""End-to-end adversarial safety tests for Apex V3 consumption.

Batches 3A + 3B wired ``eta_v3_voices`` into Quant and PM. At unit
level each agent's fold-in was shown to preserve verdicts. This
module locks the *integration* invariant: running all six agents in
sequence through the real ``run_six_stage_review`` shim, no amount
of Apex agreement can flip a KILL to a GO, and no amount of Apex
disagreement can flip a GO to a KILL.

These tests run the full pipeline in memory — no journal, no venue,
no I/O — but they import the real agent classes via the bridge shim,
so if the shim or any stage regresses the verdict contract, this
test fires.
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
        "the_firm_complete package not on disk — adversarial test requires it",
        allow_module_level=True,
    )
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from mnq.firm_runtime import run_six_stage_review  # noqa: E402

# ----- fixtures --------------------------------------------------------------


def _clean_spec(side: str = "long") -> dict:
    """A strategy spec that should comfortably clear all five dissenters."""
    return {
        "side": side,
        "sample_size": 500,
        "expected_expectancy_r": 0.55,
        "expected_win_rate": 0.48,
        "expected_avg_win_r": 1.8,
        "expected_avg_loss_r": 1.0,
        "oos_degradation_pct": 10,
        "entry_logic": "EMA9 cross EMA21 with volume filter",
        "stop_logic": "ATR(14) * 1.5 below entry",
        "target_logic": "2R fixed",
        "dd_kill_switch_r": -10,
        "approved_regimes": ["TREND_UP_QUIET", "TREND_UP_VOL", "RANGE_QUIET"],
        "sessions_traded": ["RTH"],
        "risk_per_trade_pct": 0.0025,
        "metadata": {"slippage_modeled": True},
    }


def _adversarial_spec(side: str = "long") -> dict:
    """A spec that is guaranteed to trip >= 3 critical Red Team attacks."""
    return {
        "side": side,
        "sample_size": 50,  # < 100 → critical (overfitting + sample)
        "expected_expectancy_r": 0.2,
        "expected_win_rate": 0.45,
        "expected_avg_win_r": 1.2,
        "expected_avg_loss_r": 1.0,
        "oos_degradation_pct": 45,  # > 25 → critical (overfitting)
        "entry_logic": "EMA9 cross EMA21",
        "stop_logic": "ATR(14)",
        "target_logic": "2R fixed",
        "dd_kill_switch_r": -5,
        "approved_regimes": ["TREND_UP_QUIET"],  # < 3 regimes → critical
        "sessions_traded": ["OVERNIGHT"],  # → critical
        "risk_per_trade_pct": 0.0025,
        "metadata": {"slippage_modeled": False},  # → critical
    }


def _clean_regime() -> dict:
    return {"canonical": "TREND_UP_QUIET", "is_transition": False}


def _voices_all_agree(
    direction: int = 1, *, fire_long: bool = True, pm_final: float = 55.0
) -> dict:
    """15/15 voices agreeing, engine live (pm_final >= 40.0)."""
    return {
        "regime": "TREND",
        "pm_final": pm_final,
        "quant_total": 8.0,
        "red_team": 1.0,
        "red_team_weighted": 1.0,
        "voice_agree": 15,
        "direction": direction,
        "fire_long": fire_long,
        "fire_short": not fire_long,
        "setup_name": "ORB_RETEST",
        "blocked_reason": "",
        "voices": {f"V{i}": 1.0 for i in range(1, 16)},
        "source": "eta_v3",
    }


def _voices_none_agree(pm_final: float = 0.0) -> dict:
    """0/15 voices agreeing, engine dead."""
    return {
        "regime": "NEUTRAL",
        "pm_final": pm_final,
        "quant_total": 0.0,
        "red_team": 3.0,
        "red_team_weighted": 3.0,
        "voice_agree": 0,
        "direction": 0,
        "fire_long": False,
        "fire_short": False,
        "setup_name": "NONE",
        "blocked_reason": "no_voices",
        "voices": {f"V{i}": 0.0 for i in range(1, 16)},
        "source": "eta_v3",
    }


def _run(
    spec: dict,
    *,
    regime: dict | None = None,
    voices: dict | None = None,
    pm_final: float | None = None,
) -> dict:
    payload: dict = {
        "spec": spec,
        "side": spec["side"],
        "market_state": {
            "current_spread_ticks": 1.0,
            "current_volume_per_min": 2000,
            "latency_ms": 150,
            "max_acceptable_latency_ms": 500,
        },
        "upcoming_catalysts": [],
    }
    if voices is not None:
        payload["eta_v3_voices"] = voices
    if pm_final is not None:
        payload["eta_v3_pm_final"] = pm_final
    return run_six_stage_review(
        strategy_id="adversarial_test",
        decision_context="eta_v3_safety",
        payload=payload,
        regime_snapshot=regime or _clean_regime(),
    )


# ----- Invariant 1: Apex cannot override a KILL ------------------------------


class TestApexCannotForceGo:
    """No matter how hard Apex agrees, a KILL-worthy spec stays KILL."""

    def test_full_apex_agreement_with_adversarial_spec_still_kills(self):
        outputs = _run(
            _adversarial_spec(),
            voices=_voices_all_agree(direction=1, pm_final=100.0),
            pm_final=100.0,
        )
        # Red Team fires >=3 critical attacks → KILL
        assert outputs["red_team"]["verdict"] == "KILL"
        # PM: any KILL wins → KILL
        assert outputs["pm"]["verdict"] == "KILL"
        # Apex was consumed at PM
        assert outputs["pm"]["payload"]["eta_v3"]["consumed"] is True
        assert outputs["pm"]["payload"]["eta_v3"]["voice_agree"] == 15
        # Probability blended but verdict held
        assert outputs["pm"]["payload"]["eta_v3"]["adjusted_probability"] < 1.0

    def test_full_apex_agreement_with_adversarial_spec_quant_still_modifies(self):
        """Quant's own verdict locks too — violations bypass the fold-in."""
        outputs = _run(
            _adversarial_spec(),
            voices=_voices_all_agree(direction=1, pm_final=100.0),
            pm_final=100.0,
        )
        # Quant sees sample_size<100 violation → MODIFY regardless of voices
        assert outputs["quant"]["verdict"] == "MODIFY"
        # Apex was still consumed (summary emitted)
        assert outputs["quant"]["payload"]["eta_v3"]["consumed"] is True

    def test_apex_agreement_does_not_shift_pm_verdict_on_same_spec(self):
        """Same spec + different Apex signals → same PM verdict, different
        probability. This is the dissent-preservation contract."""
        spec = _clean_spec()

        out_none = _run(spec, voices=None)
        out_full = _run(
            spec,
            voices=_voices_all_agree(direction=1, pm_final=100.0),
            pm_final=100.0,
        )
        out_zero = _run(
            spec,
            voices=_voices_none_agree(pm_final=0.0),
            pm_final=0.0,
        )

        # Verdicts are identical across all three — Apex is a co-signer, not a voter
        assert out_none["pm"]["verdict"] == out_full["pm"]["verdict"]
        assert out_none["pm"]["verdict"] == out_zero["pm"]["verdict"]

        # Probabilities DIFFER — that's the point of the fold-in
        p_none = out_none["pm"]["probability"]
        p_full = out_full["pm"]["probability"]
        p_zero = out_zero["pm"]["probability"]
        # If verdict is go-like, full > none > zero. Otherwise probabilities
        # still shift but direction depends on base prob.
        assert p_full != p_zero
        # When verdict is GO, full agreement + engine live should lift above baseline
        if out_full["pm"]["verdict"] == "GO":
            assert p_full > p_none
            assert p_full > p_zero


# ----- Invariant 2: Apex disagreement cannot force a KILL --------------------


class TestApexCannotForceKill:
    """0/15 voices cannot shove a clean spec into KILL."""

    def test_zero_agreement_on_clean_spec_does_not_kill(self):
        outputs = _run(
            _clean_spec(),
            voices=_voices_none_agree(pm_final=0.0),
            pm_final=0.0,
        )
        # Apex was consumed
        assert outputs["pm"]["payload"]["eta_v3"]["consumed"] is True
        assert outputs["pm"]["payload"]["eta_v3"]["voice_agree"] == 0
        # But PM does NOT kill — clean spec survives a silent Apex
        assert outputs["pm"]["verdict"] != "KILL"

    def test_short_apex_vs_long_spec_does_not_flip_verdict(self):
        """Apex screams SHORT on a LONG spec — direction conflict is recorded
        as a penalty, not a verdict flip."""
        voices = _voices_all_agree(direction=-1, fire_long=False, pm_final=60.0)
        outputs = _run(
            _clean_spec(side="long"),
            voices=voices,
            pm_final=60.0,
        )
        pm_apex = outputs["pm"]["payload"]["eta_v3"]
        assert pm_apex["consumed"] is True
        assert pm_apex["verdict_alignment_label"] == "CONFLICT"
        # PM verdict is driven by 5-stage tally, not Apex direction
        assert outputs["pm"]["verdict"] != "KILL"


# ----- Invariant 3: Red Team is mandatory + Apex doesn't replace it ---------


class TestRedTeamMandatoryUnderApex:
    """PM refuses to ship without Red Team attacks, regardless of Apex state."""

    def test_pm_sees_red_team_attacks_in_enriched_payload(self):
        outputs = _run(
            _clean_spec(),
            voices=_voices_all_agree(direction=1, pm_final=60.0),
            pm_final=60.0,
        )
        # Red Team always produces at least the process-violation attack
        # when the spec is clean — attacks list is non-empty
        assert len(outputs["red_team"]["payload"]["attacks"]) >= 1

    def test_apex_enrichment_does_not_erase_agent_outputs(self):
        """The enriched payload is pass-through — agent_outputs dict must
        arrive at PM with all five prior stages."""
        outputs = _run(
            _clean_spec(),
            voices=_voices_all_agree(direction=1, pm_final=60.0),
            pm_final=60.0,
        )
        # PM emits all_verdicts covering all 5 prior stages
        all_verdicts = outputs["pm"]["payload"]["all_verdicts"]
        assert set(all_verdicts.keys()) == {"quant", "red_team", "risk", "macro", "micro"}


# ----- Invariant 4: Apex summaries flow through to PM ------------------------


class TestApexFlowsEndToEnd:
    """Both stages that consume Apex V3 emit their summary in payload."""

    def test_quant_payload_contains_apex_summary(self):
        outputs = _run(
            _clean_spec(),
            voices=_voices_all_agree(direction=1, pm_final=60.0),
            pm_final=60.0,
        )
        assert "eta_v3" in outputs["quant"]["payload"]
        assert outputs["quant"]["payload"]["eta_v3"]["consumed"] is True

    def test_pm_payload_contains_apex_summary(self):
        outputs = _run(
            _clean_spec(),
            voices=_voices_all_agree(direction=1, pm_final=60.0),
            pm_final=60.0,
        )
        assert "eta_v3" in outputs["pm"]["payload"]
        assert outputs["pm"]["payload"]["eta_v3"]["consumed"] is True

    def test_no_apex_voices_both_stages_no_op(self):
        """Pipeline must work identically when eta_v3_voices is absent."""
        outputs = _run(_clean_spec(), voices=None, pm_final=None)
        # Both stages report consumed=False; neither verdict shifts
        assert outputs["quant"]["payload"]["eta_v3"]["consumed"] is False
        assert outputs["pm"]["payload"]["eta_v3"]["consumed"] is False
        # Delta is zero end-to-end
        assert outputs["quant"]["payload"]["eta_v3"]["delta"] == 0.0
        assert outputs["pm"]["payload"]["eta_v3"]["delta"] == 0.0


# ----- Invariant 5: Engine-below-gate is respected at integration ------------


class TestEngineGateAtIntegration:
    """pm_final below APEX_V3_PM_GATE should not grant the agreement bonus."""

    def test_pm_final_below_gate_blocks_bonus(self):
        # Full voice agreement but pm_final=5.0 < 40.0 gate → no bonus
        voices = _voices_all_agree(direction=1, pm_final=5.0)
        outputs = _run(
            _clean_spec(),
            voices=voices,
            pm_final=5.0,
        )
        pm_apex = outputs["pm"]["payload"]["eta_v3"]
        assert pm_apex["engine_live"] is False
        assert pm_apex["bonus_applied"] == 0.0

    def test_pm_final_above_gate_grants_bonus_on_go(self):
        voices = _voices_all_agree(direction=1, pm_final=60.0)
        outputs = _run(
            _clean_spec(),
            voices=voices,
            pm_final=60.0,
        )
        pm_apex = outputs["pm"]["payload"]["eta_v3"]
        # Only asserted when PM final verdict was GO/MODIFY — if pipeline
        # landed on HOLD or KILL, bonus is 0 by design
        if outputs["pm"]["verdict"] in ("GO", "MODIFY"):
            assert pm_apex["engine_live"] is True
            assert pm_apex["bonus_applied"] > 0.0
