"""Pure-mapping layer: Apex V3 firm_engine output → Firm AgentInput payload.

Keeps two worlds decoupled:

  - eta_v3_framework.python.firm_engine produces ``FirmDecision`` with
    15 voice scores, regime, red_team, pm_final.

  - firm.agents.base.AgentInput carries ``payload`` (a plain dict) that
    the 6-stage review chain consumes. Quant agent reads whatever is
    present; unknown keys are ignored by design.

This module exposes:

  1. ``run_apex_evaluation(bar, setup, regime)`` — calls into the V3
     engine and returns a snapshot. Tolerant of import failure — if the
     eta_v3_framework package isn't on sys.path, returns None.

  2. ``ApexVoiceSnapshot`` — a frozen dataclass representing the
     trimmed-down view the Firm actually needs.

  3. ``apex_to_firm_payload(base, snapshot)`` — returns a NEW dict with
     ``eta_v3_voices`` added. Never mutates ``base``.

  4. ``summarize_voices(snapshot)`` — single-line diagnostic string.

The adapter does NOT import the_firm_complete. It only shapes a dict
that the existing bridge shim (src/mnq/firm_runtime.py) will pass
through verbatim.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
APEX_V3_PY = REPO_ROOT / "eta_v3_framework" / "python"


@dataclass(frozen=True, slots=True)
class ApexVoiceSnapshot:
    """Trimmed 15-voice result plus regime and PM aggregate.

    Kept small on purpose — AgentInput payloads are passed through
    the journal, so bloat there means bigger events.db.
    """

    regime: str
    pm_final: float
    quant_total: float
    red_team: float
    red_team_weighted: float
    voice_agree: int
    direction: int
    fire_long: bool
    fire_short: bool
    setup_name: str
    blocked_reason: str
    voices: dict[str, float] = field(default_factory=dict)
    source: str = "eta_v3"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Cast voices floats to plain floats (asdict already does so
        # for dicts of primitives, but guard against numpy scalars).
        d["voices"] = {k: float(v) for k, v in d["voices"].items()}
        return d


def _ensure_eta_v3_on_path() -> bool:
    """Add eta_v3_framework.python to sys.path if not there.

    Returns True if the package is importable, False otherwise.
    """
    if not APEX_V3_PY.exists():
        return False
    p = str(APEX_V3_PY)
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        import firm_engine  # noqa: F401

        return True
    except ImportError:
        return False


def probe_eta_v3_engine() -> dict[str, Any]:
    """Lightweight probe — returns what's importable / what's not.

    Useful for reporter scripts that want to show whether the adapter
    is currently wired or fallback-stubbed.
    """
    if not APEX_V3_PY.exists():
        return {"available": False, "reason": "eta_v3_framework/python not present"}
    if not _ensure_eta_v3_on_path():
        return {"available": False, "reason": "firm_engine import failed"}
    try:
        import firm_engine  # type: ignore

        voices = [n for n in dir(firm_engine) if n.startswith("voice_")]
        return {
            "available": True,
            "voices_found": len(voices),
            "voice_names": voices,
            "has_evaluate": hasattr(firm_engine, "evaluate"),
            "has_detect_regime": hasattr(firm_engine, "detect_regime"),
        }
    except Exception as e:  # pragma: no cover — defensive
        return {"available": False, "reason": f"probe raised {type(e).__name__}: {e}"}


def run_apex_evaluation(
    bar: Any, setup: Any, regime: str = "NEUTRAL", **kwargs: Any
) -> ApexVoiceSnapshot | None:
    """Call eta_v3_framework.firm_engine.evaluate and package result.

    ``bar`` must be (or duck-type) firm_engine.Bar.
    ``setup`` must be (or duck-type) firm_engine.SetupTriggers.

    Returns None if the engine isn't available. Callers should treat
    that as "no enrichment" and pass the base payload through unchanged.
    """
    if not _ensure_eta_v3_on_path():
        return None
    try:
        import firm_engine  # type: ignore
    except ImportError:
        return None
    try:
        decision = firm_engine.evaluate(bar, setup, regime, **kwargs)
    except Exception:
        return None

    return ApexVoiceSnapshot(
        regime=getattr(decision, "regime", regime),
        pm_final=float(getattr(decision, "pm_final", 0.0)),
        quant_total=float(getattr(decision, "quant_total", 0.0)),
        red_team=float(getattr(decision, "red_team", 0.0)),
        red_team_weighted=float(getattr(decision, "red_team_weighted", 0.0)),
        voice_agree=int(getattr(decision, "voice_agree", 0)),
        direction=int(getattr(decision, "direction", 0)),
        fire_long=bool(getattr(decision, "fire_long", False)),
        fire_short=bool(getattr(decision, "fire_short", False)),
        setup_name=str(getattr(decision, "setup_name", "")),
        blocked_reason=str(getattr(decision, "blocked_reason", "")),
        voices={k: float(v) for k, v in getattr(decision, "voices", {}).items()},
    )


def apex_to_firm_payload(
    base_payload: dict[str, Any], snapshot: ApexVoiceSnapshot | None
) -> dict[str, Any]:
    """Return a NEW dict with eta_v3_voices enrichment added.

    If ``snapshot`` is None (engine unavailable), returns the base
    payload unchanged. Never mutates the input.
    """
    if snapshot is None:
        return dict(base_payload)
    enriched = dict(base_payload)
    enriched["eta_v3_voices"] = snapshot.as_dict()
    # Convenience duplications the Quant agent may read without
    # reaching into the nested dict.
    enriched.setdefault("eta_v3_pm_final", snapshot.pm_final)
    enriched.setdefault("eta_v3_regime", snapshot.regime)
    enriched.setdefault("eta_v3_direction", snapshot.direction)
    return enriched


def summarize_voices(snapshot: ApexVoiceSnapshot | None) -> str:
    """Single-line diagnostic, safe for logs and reporter scripts."""
    if snapshot is None:
        return "eta_v3: unavailable"
    dir_str = {-1: "SHORT", 0: "FLAT", 1: "LONG"}.get(snapshot.direction, "?")
    gate = "FIRE" if (snapshot.fire_long or snapshot.fire_short) else "HOLD"
    return (
        f"eta_v3: {gate} {dir_str} · regime={snapshot.regime} · "
        f"pm_final={snapshot.pm_final:+.1f} · quant={snapshot.quant_total:+.1f} · "
        f"red={snapshot.red_team:.1f} · agree={snapshot.voice_agree}/15 · "
        f"setup={snapshot.setup_name or '—'}"
    )


# Convenience alias — the __init__ exposes this name, but callers may
# also want to build enrichment without running a full evaluation
# (e.g., during testing with a handcrafted snapshot).
def build_enrichment_payload(
    base_payload: dict[str, Any], snapshot: ApexVoiceSnapshot | None
) -> dict[str, Any]:
    return apex_to_firm_payload(base_payload, snapshot)


def enrich_agent_input(agent_input: Any, snapshot: ApexVoiceSnapshot | None) -> Any:
    """In-place-safe enrichment of a firm.agents.base.AgentInput.

    Rather than reaching into AgentInput's dataclass, we rebuild its
    payload attribute. Works for any AgentInput that exposes ``.payload``.
    """
    if snapshot is None or agent_input is None:
        return agent_input
    payload = getattr(agent_input, "payload", None)
    if not isinstance(payload, dict):
        return agent_input
    agent_input.payload = apex_to_firm_payload(payload, snapshot)
    return agent_input
