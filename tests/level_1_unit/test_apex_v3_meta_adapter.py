"""Unit tests for the Apex V3 Meta-Firm adapter.

Mirror of ``test_eta_v3_adapter.py``. The meta adapter is a pure mapping
layer from ``firm_meta.MetaDecision`` to a payload fragment the
orchestrator reads. These tests lock in:

  - fail-open when firm_meta isn't on sys.path
  - non-destructive enrichment (base payload not mutated)
  - defensive casting for malformed MetaDecisions
  - strategy-param override shape
"""
from __future__ import annotations

from mnq.eta_v3 import (
    MetaSnapshot,
    apply_meta_overrides,
    build_meta_context,
    meta_to_firm_payload,
    probe_meta_firm_engine,
    run_meta_evaluation,
    summarize_meta,
)


def _fake_snapshot(**overrides) -> MetaSnapshot:
    base = {
        "regime_vote": "NEUTRAL",
        "pm_threshold": 32.5,
        "enabled_setups": ["ORB", "EMA PB", "SWEEP"],
        "risk_budget_R": 2.0,
        "size_multiplier": 1.0,
        "trade_allowed": True,
        "confidence": 55.0,
        "reason": "TRADE: meta-confidence 55/100, 3 setups active",
        "voices": {"regime_stability": 40.0, "drawdown_check": 0.0},
        "audit": {"regime_vote": "vol=+40, stab=+40 -> NEUTRAL"},
    }
    base.update(overrides)
    return MetaSnapshot(**base)


# ─────────────────────────────────────────────────────────────────────────
# Probe
# ─────────────────────────────────────────────────────────────────────────

class TestProbe:
    def test_probe_returns_dict_with_available_key(self):
        r = probe_meta_firm_engine()
        assert isinstance(r, dict)
        assert "available" in r
        if r["available"]:
            assert "voices_found" in r
            assert isinstance(r["voices_found"], int)
            assert r.get("has_run_meta_firm") is True
            assert r.get("has_meta_context") is True
        else:
            assert "reason" in r


# ─────────────────────────────────────────────────────────────────────────
# build_meta_context / run_meta_evaluation
# ─────────────────────────────────────────────────────────────────────────

class TestBuildContext:
    def test_build_without_framework_returns_none_gracefully(self):
        # Either the framework isn't importable (None) or a valid
        # MetaContext is returned — both acceptable outcomes.
        ctx = build_meta_context(consecutive_losses=1, rolling_pf=1.3)
        assert ctx is None or ctx.__class__.__name__ == "MetaContext"

    def test_unknown_fields_dropped_silently(self):
        # The helper filters to a known allowlist; unknown kwargs must
        # not bubble up as TypeError to the caller.
        ctx = build_meta_context(
            consecutive_losses=2,
            garbage_field="should_be_dropped",
            another_unknown=42,
        )
        # If construction succeeded, the known field made it through.
        if ctx is not None:
            assert getattr(ctx, "consecutive_losses", None) == 2


class TestRunEvaluation:
    def test_run_with_none_context_returns_none(self):
        assert run_meta_evaluation(None) is None

    def test_run_without_framework_returns_none(self):
        # Deliberately pass an object that can't be used as MetaContext
        # to force the engine path to bail.
        snap = run_meta_evaluation(object())
        assert snap is None or isinstance(snap, MetaSnapshot)


# ─────────────────────────────────────────────────────────────────────────
# Payload enrichment
# ─────────────────────────────────────────────────────────────────────────

class TestPayloadEnrichment:
    def test_none_snapshot_returns_base_unchanged_copy(self):
        base = {"symbol": "MNQ", "side": "long"}
        out = meta_to_firm_payload(base, None)
        assert out == base
        assert out is not base  # must be a copy

    def test_none_snapshot_does_not_mutate_input(self):
        base = {"symbol": "MNQ"}
        meta_to_firm_payload(base, None)
        assert base == {"symbol": "MNQ"}

    def test_snapshot_adds_eta_v3_meta_key(self):
        base = {"symbol": "MNQ"}
        snap = _fake_snapshot()
        out = meta_to_firm_payload(base, snap)
        assert "eta_v3_meta" in out
        assert out["eta_v3_meta"]["regime_vote"] == "NEUTRAL"
        assert out["eta_v3_meta"]["pm_threshold"] == 32.5

    def test_snapshot_adds_convenience_keys(self):
        snap = _fake_snapshot(
            trade_allowed=False,
            pm_threshold=45.0,
            size_multiplier=0.5,
            risk_budget_R=1.0,
            regime_vote="RISK-OFF",
        )
        out = meta_to_firm_payload({}, snap)
        assert out["eta_v3_meta_trade_allowed"] is False
        assert out["eta_v3_meta_pm_threshold"] == 45.0
        assert out["eta_v3_meta_size_multiplier"] == 0.5
        assert out["eta_v3_meta_risk_budget_R"] == 1.0
        assert out["eta_v3_meta_regime_vote"] == "RISK-OFF"

    def test_base_keys_preserved_when_enriched(self):
        base = {"symbol": "MNQ", "trace_id": "abc-123", "side": "long"}
        out = meta_to_firm_payload(base, _fake_snapshot())
        for k in base:
            assert out[k] == base[k]

    def test_snapshot_as_dict_is_plain_python(self):
        snap = _fake_snapshot()
        d = snap.as_dict()
        assert d["source"] == "eta_v3_meta"
        assert isinstance(d["voices"], dict)
        assert isinstance(d["audit"], dict)
        assert isinstance(d["enabled_setups"], list)

    def test_preexisting_convenience_keys_not_overwritten(self):
        # setdefault semantics: if the caller already supplied a key
        # with that name, the adapter must not clobber it.
        base = {"eta_v3_meta_pm_threshold": 99.0}
        out = meta_to_firm_payload(base, _fake_snapshot(pm_threshold=30.0))
        assert out["eta_v3_meta_pm_threshold"] == 99.0


# ─────────────────────────────────────────────────────────────────────────
# Strategy-param override
# ─────────────────────────────────────────────────────────────────────────

class TestApplyOverrides:
    def test_none_snapshot_returns_base_copy(self):
        base = {"pm_gate": 40.0, "size_multiplier": 1.0}
        out = apply_meta_overrides(base, None)
        assert out == base
        assert out is not base

    def test_snapshot_overrides_pm_gate(self):
        out = apply_meta_overrides({"pm_gate": 40.0},
                                   _fake_snapshot(pm_threshold=25.0))
        assert out["pm_gate"] == 25.0

    def test_snapshot_overrides_size_and_budget(self):
        out = apply_meta_overrides(
            {"size_multiplier": 1.0, "daily_loss_cap_r": 3.0},
            _fake_snapshot(size_multiplier=0.5, risk_budget_R=1.0),
        )
        assert out["size_multiplier"] == 0.5
        assert out["daily_loss_cap_r"] == 1.0

    def test_snapshot_overrides_allowed_setups(self):
        out = apply_meta_overrides(
            {"allowed_setups": ["ORB", "EMA PB", "SWEEP"]},
            _fake_snapshot(enabled_setups=["ORB"]),
        )
        assert out["allowed_setups"] == ["ORB"]

    def test_trade_allowed_false_still_returns_full_override_dict(self):
        # When the meta-firm pauses the day, we still want the caller
        # to see the override dict populated for telemetry — the
        # honouring of trade_allowed is the caller's responsibility.
        out = apply_meta_overrides({},
                                   _fake_snapshot(trade_allowed=False,
                                                  pm_threshold=50.0,
                                                  size_multiplier=0.0))
        assert out["trade_allowed"] is False
        assert out["pm_gate"] == 50.0
        assert out["size_multiplier"] == 0.0


# ─────────────────────────────────────────────────────────────────────────
# Summarise
# ─────────────────────────────────────────────────────────────────────────

class TestSummarize:
    def test_none_returns_unavailable_label(self):
        assert "unavailable" in summarize_meta(None)

    def test_snapshot_trade_allowed_returns_trade_tag(self):
        s = _fake_snapshot(trade_allowed=True)
        assert "TRADE" in summarize_meta(s)

    def test_snapshot_trade_blocked_returns_pause_tag(self):
        s = _fake_snapshot(trade_allowed=False)
        assert "PAUSE" in summarize_meta(s)

    def test_includes_pm_and_size(self):
        s = _fake_snapshot(pm_threshold=37.5, size_multiplier=0.75)
        out = summarize_meta(s)
        assert "37.5" in out
        assert "0.75" in out

    def test_includes_regime_vote(self):
        s = _fake_snapshot(regime_vote="RISK-OFF")
        assert "RISK-OFF" in summarize_meta(s)


# ─────────────────────────────────────────────────────────────────────────
# Integration: per-trade adapter + meta adapter compose cleanly
# ─────────────────────────────────────────────────────────────────────────

class TestComposition:
    def test_per_trade_and_meta_both_enrich_same_payload(self):
        # This simulates what firm_live_review does: first fold in
        # per-trade voices, then fold in meta-level overrides. Both
        # fragments must coexist in the final payload without either
        # clobbering the other.
        from mnq.eta_v3 import ApexVoiceSnapshot, apex_to_firm_payload

        voice_snap = ApexVoiceSnapshot(
            regime="NEUTRAL",
            pm_final=42.0,
            quant_total=50.0,
            red_team=10.0,
            red_team_weighted=5.0,
            voice_agree=11,
            direction=1,
            fire_long=True,
            fire_short=False,
            setup_name="ORB",
            blocked_reason="",
            voices={},
        )
        meta_snap = _fake_snapshot()
        base = {"symbol": "MNQ", "side": "long", "trace_id": "t1"}

        stage_1 = apex_to_firm_payload(base, voice_snap)
        stage_2 = meta_to_firm_payload(stage_1, meta_snap)

        # Both fragments present, no mutation of earlier stages.
        assert "eta_v3_voices" in stage_2
        assert "eta_v3_meta" in stage_2
        assert stage_2["symbol"] == "MNQ"
        assert "eta_v3_voices" not in base
        assert "eta_v3_meta" not in stage_1
