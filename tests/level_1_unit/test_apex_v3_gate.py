"""Unit tests for the Apex V3 downstream gate (``mnq.eta_v3.gate``).

The gate is a pure function: PM output dict → {action, size_mult, reason}.
These tests exercise:

- The four delta zones (strong dissent / soft dissent / neutral / corroborate)
- PM verdict authority (KILL / HOLD always skip; MODIFY trimmed under neutral)
- Fail-safe paths (missing payload, non-consumed Apex, malformed delta)
- Symmetry of the skip decision across invalid inputs
"""

from __future__ import annotations

from mnq.eta_v3.gate import (
    DELTA_SOFT_DISSENT,
    DELTA_STRONG_DISSENT,
    SIZE_FULL,
    SIZE_REDUCED,
    SIZE_SKIP,
    GateAction,
    apex_gate,
)

# --- fixtures ----------------------------------------------------------------


def _pm_output(
    *,
    verdict: str = "GO",
    delta: float = 0.0,
    consumed: bool = True,
    base_probability: float = 0.6,
    adjusted_probability: float | None = None,
) -> dict:
    if adjusted_probability is None:
        adjusted_probability = base_probability + delta
    return {
        "verdict": verdict,
        "probability": adjusted_probability,
        "payload": {
            "eta_v3": {
                "consumed": consumed,
                "delta": delta,
                "base_probability": base_probability,
                "adjusted_probability": adjusted_probability,
                "voice_agree": 10,
                "pm_final": 50.0,
                "engine_live": True,
            },
        },
    }


# --- Delta-zone routing -------------------------------------------------------


class TestDeltaZones:
    """The four delta bands the gate uses to decide size."""

    def test_strong_dissent_skips(self):
        out = _pm_output(verdict="GO", delta=-0.20)
        g = apex_gate(out)
        assert g["action"] == GateAction.SKIP
        assert g["size_mult"] == SIZE_SKIP
        assert "strong_dissent" in g["reason"]

    def test_just_below_strong_threshold_skips(self):
        # Just below -0.10 → skip (strict inequality)
        out = _pm_output(verdict="GO", delta=DELTA_STRONG_DISSENT - 0.0001)
        assert apex_gate(out)["action"] == GateAction.SKIP

    def test_at_strong_threshold_reduces_not_skips(self):
        # delta == -0.10 is NOT strong dissent (inclusive lower bound of reduced)
        out = _pm_output(verdict="GO", delta=DELTA_STRONG_DISSENT)
        g = apex_gate(out)
        assert g["action"] == GateAction.REDUCED
        assert g["size_mult"] == SIZE_REDUCED
        assert "soft_dissent" in g["reason"]

    def test_soft_dissent_reduces(self):
        out = _pm_output(verdict="GO", delta=-0.07)
        g = apex_gate(out)
        assert g["action"] == GateAction.REDUCED
        assert g["size_mult"] == SIZE_REDUCED

    def test_just_below_soft_threshold_reduces(self):
        out = _pm_output(verdict="GO", delta=DELTA_SOFT_DISSENT - 0.0001)
        assert apex_gate(out)["action"] == GateAction.REDUCED

    def test_at_soft_threshold_neutral_zone_on_go(self):
        # delta == -0.05 is NOT soft dissent (strict inequality: delta < -0.05)
        out = _pm_output(verdict="GO", delta=DELTA_SOFT_DISSENT)
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL
        assert "neutral" in g["reason"]

    def test_neutral_on_go_ships_full(self):
        out = _pm_output(verdict="GO", delta=0.00)
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL
        assert g["size_mult"] == SIZE_FULL

    def test_active_corroborate_ships_full(self):
        out = _pm_output(verdict="GO", delta=0.08)
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL
        assert "corroborates" in g["reason"]

    def test_just_above_corroborate_threshold(self):
        out = _pm_output(verdict="GO", delta=0.021)
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL
        assert "corroborates" in g["reason"]


# --- PM verdict authority -----------------------------------------------------


class TestPMVerdictAuthority:
    """Only GO/MODIFY ever ship. Everything else skips regardless of delta."""

    def test_kill_verdict_always_skips(self):
        # Even a strongly positive delta can't flip a KILL
        out = _pm_output(verdict="KILL", delta=+0.30)
        g = apex_gate(out)
        assert g["action"] == GateAction.SKIP
        assert "kill" in g["reason"]

    def test_hold_verdict_always_skips(self):
        out = _pm_output(verdict="HOLD", delta=+0.30)
        g = apex_gate(out)
        assert g["action"] == GateAction.SKIP

    def test_unknown_verdict_skips(self):
        out = _pm_output(verdict="WTF", delta=+0.10)
        g = apex_gate(out)
        assert g["action"] == GateAction.SKIP

    def test_modify_with_corroboration_ships_full(self):
        out = _pm_output(verdict="MODIFY", delta=+0.05)
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL

    def test_modify_in_neutral_zone_reduced(self):
        out = _pm_output(verdict="MODIFY", delta=+0.00)
        g = apex_gate(out)
        assert g["action"] == GateAction.REDUCED
        assert "modify" in g["reason"]

    def test_modify_with_soft_dissent_reduced(self):
        out = _pm_output(verdict="MODIFY", delta=-0.07)
        g = apex_gate(out)
        assert g["action"] == GateAction.REDUCED

    def test_modify_with_strong_dissent_skips(self):
        out = _pm_output(verdict="MODIFY", delta=-0.15)
        g = apex_gate(out)
        assert g["action"] == GateAction.SKIP


# --- Fail-safe paths ----------------------------------------------------------


class TestFailSafe:
    """Malformed / missing input produces deterministic decisions."""

    def test_none_input_skips(self):
        g = apex_gate(None)
        assert g["action"] == GateAction.SKIP
        assert "missing_or_invalid" in g["reason"]

    def test_empty_dict_input_skips(self):
        g = apex_gate({})
        assert g["action"] == GateAction.SKIP

    def test_missing_payload_falls_back_to_full_on_go(self):
        # PM verdict GO but no payload → no apex signal, ship full
        g = apex_gate({"verdict": "GO"})
        assert g["action"] == GateAction.FULL
        assert "no_apex_signal" in g["reason"]

    def test_payload_without_eta_v3_falls_back(self):
        out = {"verdict": "GO", "payload": {"other": "junk"}}
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL
        assert "no_apex_signal" in g["reason"]

    def test_apex_not_consumed_falls_back(self):
        out = _pm_output(verdict="GO", delta=-0.50, consumed=False)
        # consumed=False → gate falls back, delta is ignored
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL
        assert "no_apex_signal" in g["reason"]

    def test_malformed_delta_treated_as_zero(self):
        out = _pm_output(verdict="GO", delta=0.0)
        out["payload"]["eta_v3"]["delta"] = "not a number"
        g = apex_gate(out)
        # Treated as 0.0 → neutral zone on GO → full
        assert g["action"] == GateAction.FULL

    def test_nan_delta_treated_as_zero(self):
        out = _pm_output(verdict="GO", delta=0.0)
        out["payload"]["eta_v3"]["delta"] = float("nan")
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL

    def test_non_dict_payload_falls_back(self):
        out = {"verdict": "GO", "payload": "not_a_dict"}
        g = apex_gate(out)
        assert g["action"] == GateAction.FULL


# --- Decision shape contract --------------------------------------------------


class TestDecisionShape:
    """Every decision returns the same three keys with expected types."""

    def test_all_decisions_have_required_keys(self):
        cases = [
            None,
            {},
            _pm_output(verdict="KILL", delta=0.0),
            _pm_output(verdict="GO", delta=+0.10),
            _pm_output(verdict="GO", delta=-0.20),
            _pm_output(verdict="MODIFY", delta=0.0),
        ]
        for c in cases:
            d = apex_gate(c)
            assert set(d.keys()) == {"action", "size_mult", "reason"}
            assert isinstance(d["action"], str)
            assert isinstance(d["size_mult"], float)
            assert isinstance(d["reason"], str)
            assert d["action"] in (GateAction.FULL, GateAction.REDUCED, GateAction.SKIP)
            assert d["size_mult"] in (SIZE_FULL, SIZE_REDUCED, SIZE_SKIP)

    def test_size_mult_matches_action(self):
        cases = [
            (GateAction.FULL, SIZE_FULL, _pm_output(verdict="GO", delta=+0.10)),
            (GateAction.REDUCED, SIZE_REDUCED, _pm_output(verdict="GO", delta=-0.07)),
            (GateAction.SKIP, SIZE_SKIP, _pm_output(verdict="GO", delta=-0.25)),
            (GateAction.SKIP, SIZE_SKIP, _pm_output(verdict="KILL", delta=0.0)),
        ]
        for expected_action, expected_size, pm_out in cases:
            d = apex_gate(pm_out)
            assert d["action"] == expected_action
            assert d["size_mult"] == expected_size
