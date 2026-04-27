"""Unit tests for the Apex V3 → Firm payload adapter.

The adapter is a pure mapping layer — these tests exercise its public
surface without requiring the eta_v3_framework package to be on
sys.path. When the real engine isn't importable, the adapter returns
None snapshots and the enrichment helpers pass the base payload
through unchanged. That fail-open contract is what these tests lock in.
"""

from __future__ import annotations

from mnq.eta_v3 import (
    ApexVoiceSnapshot,
    apex_to_firm_payload,
    build_enrichment_payload,
    enrich_agent_input,
    probe_eta_v3_engine,
    run_apex_evaluation,
    summarize_voices,
)


def _fake_snapshot(**overrides) -> ApexVoiceSnapshot:
    base = {
        "regime": "NEUTRAL",
        "pm_final": 42.5,
        "quant_total": 55.0,
        "red_team": 12.0,
        "red_team_weighted": 8.5,
        "voice_agree": 11,
        "direction": 1,
        "fire_long": True,
        "fire_short": False,
        "setup_name": "ORB_LONG",
        "blocked_reason": "",
        "voices": {"V1_ORB": 4.0, "V3_EMA_TREND": 3.5},
    }
    base.update(overrides)
    return ApexVoiceSnapshot(**base)


class TestProbe:
    def test_probe_returns_dict_with_available_key(self):
        r = probe_eta_v3_engine()
        assert isinstance(r, dict)
        assert "available" in r
        # Either path is valid — framework may or may not be on disk.
        if r["available"]:
            assert "voices_found" in r
            assert isinstance(r["voices_found"], int)
        else:
            assert "reason" in r


class TestRunEvaluation:
    def test_run_without_framework_returns_none_gracefully(self):
        # Crafted garbage input that would crash any real engine —
        # the adapter should catch and return None instead of raising.
        snapshot = run_apex_evaluation(bar=None, setup=None, regime="UNKNOWN")
        # Either the framework isn't there (None), or a real engine
        # was called and errored on None inputs (also None). Both OK.
        assert snapshot is None or isinstance(snapshot, ApexVoiceSnapshot)


class TestPayloadEnrichment:
    def test_none_snapshot_returns_base_unchanged_copy(self):
        base = {"symbol": "MNQ", "side": "long", "qty": 1}
        out = apex_to_firm_payload(base, None)
        assert out == base
        assert out is not base  # must be a copy, not the same dict

    def test_none_snapshot_does_not_mutate_input(self):
        base = {"symbol": "MNQ"}
        apex_to_firm_payload(base, None)
        assert base == {"symbol": "MNQ"}

    def test_snapshot_adds_eta_v3_voices_key(self):
        base = {"symbol": "MNQ", "side": "long"}
        snap = _fake_snapshot()
        out = apex_to_firm_payload(base, snap)
        assert "eta_v3_voices" in out
        assert out["eta_v3_voices"]["regime"] == "NEUTRAL"
        assert out["eta_v3_voices"]["pm_final"] == 42.5

    def test_snapshot_adds_convenience_keys(self):
        snap = _fake_snapshot(pm_final=67.0, regime="TRENDING", direction=-1)
        out = apex_to_firm_payload({}, snap)
        assert out["eta_v3_pm_final"] == 67.0
        assert out["eta_v3_regime"] == "TRENDING"
        assert out["eta_v3_direction"] == -1

    def test_base_keys_preserved_when_enriched(self):
        base = {"symbol": "MNQ", "side": "long", "trace_id": "abc-123"}
        out = apex_to_firm_payload(base, _fake_snapshot())
        for k in base:
            assert out[k] == base[k]

    def test_convenience_aliases_equal_primary_builder(self):
        base = {"symbol": "MNQ"}
        snap = _fake_snapshot()
        assert apex_to_firm_payload(base, snap) == build_enrichment_payload(base, snap)

    def test_snapshot_as_dict_contains_voices(self):
        snap = _fake_snapshot()
        d = snap.as_dict()
        assert d["voices"] == {"V1_ORB": 4.0, "V3_EMA_TREND": 3.5}
        assert d["source"] == "eta_v3"


class TestEnrichAgentInput:
    def test_none_snapshot_is_noop(self):
        class _AI:
            payload = {"x": 1}

        ai = _AI()
        out = enrich_agent_input(ai, None)
        assert out is ai
        assert ai.payload == {"x": 1}

    def test_none_agent_input_is_noop(self):
        assert enrich_agent_input(None, _fake_snapshot()) is None

    def test_non_dict_payload_is_noop(self):
        class _AI:
            payload = "not-a-dict"

        ai = _AI()
        enrich_agent_input(ai, _fake_snapshot())
        assert ai.payload == "not-a-dict"

    def test_rewrites_payload_with_enrichment(self):
        class _AI:
            payload = {"symbol": "MNQ"}

        ai = _AI()
        enrich_agent_input(ai, _fake_snapshot())
        assert "eta_v3_voices" in ai.payload
        assert ai.payload["symbol"] == "MNQ"


class TestSummarize:
    def test_none_returns_unavailable_label(self):
        assert "unavailable" in summarize_voices(None)

    def test_snapshot_returns_fire_for_fire_long(self):
        s = _fake_snapshot(fire_long=True, direction=1)
        out = summarize_voices(s)
        assert "FIRE" in out
        assert "LONG" in out

    def test_snapshot_returns_hold_when_neither_fires(self):
        s = _fake_snapshot(fire_long=False, fire_short=False, direction=0)
        out = summarize_voices(s)
        assert "HOLD" in out
        assert "FLAT" in out

    def test_includes_pm_final_and_regime(self):
        s = _fake_snapshot(pm_final=55.5, regime="CHOPPY")
        out = summarize_voices(s)
        assert "CHOPPY" in out
        assert "55.5" in out or "+55" in out
