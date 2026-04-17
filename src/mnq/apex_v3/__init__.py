"""Apex V3 → EVOLUTIONARY TRADING ALGO adapter layer.

This package converts the 15-voice Apex V3 firm_engine output into the
payload shape expected by the production Firm 6-agent adversarial review
(``firm.agents.base.AgentInput``). It is a PURE MAPPING LAYER — no trading
decisions are made here; it only enriches the payload that the real Firm
sees at the quant stage.

Usage::

    from mnq.eta_v3 import run_apex_evaluation, apex_to_firm_payload

    decision = run_apex_evaluation(bar, setup_triggers, regime=regime)
    enriched = apex_to_firm_payload(base_payload, decision)
    # enriched["eta_v3_voices"] now present; feed into AgentInput

The Meta-Firm adapter lives alongside it for the system-level layer::

    from mnq.eta_v3 import build_meta_context, run_meta_evaluation

    ctx = build_meta_context(consecutive_losses=2, rolling_pf=1.6, ...)
    meta = run_meta_evaluation(ctx, base_pm=30.0)
    enriched = meta_to_firm_payload(base_payload, meta)
    # enriched["eta_v3_meta"] now present; orchestrator reads overrides

The downstream gate reads a PM output and emits a routing decision::

    from mnq.eta_v3 import apex_gate

    decision = apex_gate(pm_output)
    # decision = {"action": "full|reduced|skip", "size_mult": float, "reason": str}
"""
from __future__ import annotations

from .adapter import (
    ApexVoiceSnapshot,
    apex_to_firm_payload,
    build_enrichment_payload,
    enrich_agent_input,
    probe_eta_v3_engine,
    run_apex_evaluation,
    summarize_voices,
)
from .gate import GateAction, apex_gate
from .meta_adapter import (
    MetaSnapshot,
    apply_meta_overrides,
    build_meta_context,
    meta_to_firm_payload,
    probe_meta_firm_engine,
    run_meta_evaluation,
    summarize_meta,
)

__all__ = [
    "ApexVoiceSnapshot",
    "GateAction",
    "MetaSnapshot",
    "apex_gate",
    "apex_to_firm_payload",
    "apply_meta_overrides",
    "build_enrichment_payload",
    "build_meta_context",
    "enrich_agent_input",
    "meta_to_firm_payload",
    "probe_eta_v3_engine",
    "probe_meta_firm_engine",
    "run_apex_evaluation",
    "run_meta_evaluation",
    "summarize_meta",
    "summarize_voices",
]
