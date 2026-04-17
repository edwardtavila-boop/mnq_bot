"""Meta-Firm (system-level) adapter layer.

Companion to ``adapter.py`` for the per-trade 15-voice engine. Where
that module converts a single-bar ``FirmDecision`` into an
``eta_v3_voices`` payload chunk, this module converts a
``firm_meta.MetaDecision`` — which is a SYSTEM-LEVEL verdict over
recent performance, regime stability, drawdown, and calendar — into a
small payload fragment the orchestrator can use to override or gate
its daily behaviour.

Outputs (under ``eta_v3_meta`` in the payload dict):

    regime_vote       str   — "RISK-ON" | "NEUTRAL" | "RISK-OFF"
    pm_threshold      float — suggested PM gate override (20..50)
    enabled_setups    list  — whitelist of setup names
    risk_budget_R     float — daily loss cap in R
    size_multiplier   float — 0.5 / 0.75 / 1.0
    trade_allowed     bool  — hard kill-switch
    confidence        float — 0..100
    reason            str   — single-line summary
    voices            dict  — 8 meta-voice scores
    audit             dict  — per-decision explainer strings

Same non-destructive contract as the per-trade adapter:

  - Never mutates ``base_payload`` — returns a new dict.
  - If ``firm_meta`` is unimportable, ``run_meta_evaluation`` returns
    ``None`` and the enrichment call is a no-op pass-through.
  - All numeric casts are defensive; malformed MetaDecisions downgrade
    to conservative defaults rather than raising.

The ``the_firm_complete`` agents are NOT imported here. Consumers
(firm_live_review, scripts/eta_v3_meta.py, run_all_phases Phase F)
read the payload fragment through the existing bridge shim.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
APEX_V3_PY = REPO_ROOT / "eta_v3_framework" / "python"

# Conservative default overrides used when the meta engine is
# unavailable. Matches firm_meta.MetaDecision defaults so downstream
# callers never see a surprise when the engine disappears.
_DEFAULT_PM_THRESHOLD = 30.0
_DEFAULT_RISK_BUDGET_R = 2.0
_DEFAULT_SIZE_MULTIPLIER = 1.0
_DEFAULT_ENABLED_SETUPS: tuple[str, ...] = ("ORB", "EMA PB", "SWEEP")


@dataclass(frozen=True, slots=True)
class MetaSnapshot:
    """Trimmed view of firm_meta.MetaDecision for orchestrator use.

    Mirrors the ``ApexVoiceSnapshot`` design: frozen, slotted, and
    JSON-serialisable via ``as_dict``. Kept intentionally small so the
    enrichment doesn't bloat AgentInput.payload or the event journal.
    """
    regime_vote: str
    pm_threshold: float
    enabled_setups: list[str]
    # Intentional mixedCase to mirror firm_meta.MetaDecision.risk_budget_R —
    # keeping the wire/struct name identical makes the adapter a direct
    # structural copy rather than a rename layer.
    risk_budget_R: float  # noqa: N815
    size_multiplier: float
    trade_allowed: bool
    confidence: float
    reason: str
    voices: dict[str, float] = field(default_factory=dict)
    audit: dict[str, str] = field(default_factory=dict)
    source: str = "eta_v3_meta"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["voices"] = {k: float(v) for k, v in d["voices"].items()}
        d["audit"] = {k: str(v) for k, v in d["audit"].items()}
        d["enabled_setups"] = list(d["enabled_setups"])
        return d


def _ensure_eta_v3_on_path() -> bool:
    """Add eta_v3_framework.python to sys.path if not there.

    Returns True if the ``firm_meta`` module is importable, False
    otherwise. Mirrors the helper in ``adapter.py`` but tests the
    meta module rather than the per-trade engine.
    """
    if not APEX_V3_PY.exists():
        return False
    p = str(APEX_V3_PY)
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        import firm_meta  # noqa: F401
        return True
    except ImportError:
        return False


def probe_meta_firm_engine() -> dict[str, Any]:
    """Lightweight probe — what's importable, what's not.

    Returns the same shape the per-trade probe does so reporter scripts
    can treat both engines identically.
    """
    if not APEX_V3_PY.exists():
        return {"available": False, "reason": "eta_v3_framework/python not present"}
    if not _ensure_eta_v3_on_path():
        return {"available": False, "reason": "firm_meta import failed"}
    try:
        import firm_meta  # type: ignore
        voices = [n for n in dir(firm_meta) if n.startswith("mv_")]
        return {
            "available": True,
            "voices_found": len(voices),
            "voice_names": voices,
            "has_run_meta_firm": hasattr(firm_meta, "run_meta_firm"),
            "has_meta_context": hasattr(firm_meta, "MetaContext"),
        }
    except Exception as e:  # pragma: no cover — defensive
        return {"available": False, "reason": f"probe raised {type(e).__name__}: {e}"}


def build_meta_context(**fields: Any) -> Any | None:
    """Construct a firm_meta.MetaContext from caller-supplied fields.

    Unknown kwargs are dropped silently — the MetaContext dataclass
    ignores what it doesn't define. Returns ``None`` when firm_meta
    isn't importable, which the downstream helpers translate into a
    pass-through.
    """
    if not _ensure_eta_v3_on_path():
        return None
    try:
        import firm_meta  # type: ignore
    except ImportError:
        return None
    # MetaContext is a plain @dataclass — accept whatever the caller
    # supplies and let dataclass fail loudly for type errors we want
    # surfaced (e.g., wrong dict type for regime_history).
    allowed = {
        "recent_trades", "recent_decisions",
        "rolling_win_rate", "rolling_pf", "rolling_dd",
        "current_equity_r", "peak_equity_r",
        "consecutive_losses", "consecutive_wins",
        "days_since_last_win", "regime_history",
        "avg_atr", "avg_adx", "avg_vol_z",
        "hour_et", "weekday", "now_utc",
    }
    clean = {k: v for k, v in fields.items() if k in allowed}
    try:
        return firm_meta.MetaContext(**clean)
    except Exception:
        return None


def run_meta_evaluation(ctx: Any, base_pm: float = _DEFAULT_PM_THRESHOLD
                        ) -> MetaSnapshot | None:
    """Call firm_meta.run_meta_firm and package the verdict.

    ``ctx`` must be (or duck-type) firm_meta.MetaContext. Accepts either
    a MetaContext built by ``build_meta_context`` or an equivalent
    instance constructed by the caller. Returns None if the engine is
    unavailable OR the call raises — fail-open, consistent with
    ``run_apex_evaluation``.
    """
    if not _ensure_eta_v3_on_path():
        return None
    try:
        import firm_meta  # type: ignore
    except ImportError:
        return None
    if ctx is None:
        return None
    try:
        decision = firm_meta.run_meta_firm(ctx, base_pm=float(base_pm))
    except Exception:
        return None

    # Defensive casting: MetaDecision's own types are trusted, but a
    # subclass or mock may hand back surprising types.
    try:
        enabled = [str(s) for s in getattr(decision, "enabled_setups", [])]
    except Exception:
        enabled = list(_DEFAULT_ENABLED_SETUPS)

    try:
        voices = {
            str(k): float(v)
            for k, v in dict(getattr(decision, "voices", {})).items()
        }
    except Exception:
        voices = {}

    try:
        audit = {
            str(k): str(v)
            for k, v in dict(getattr(decision, "audit", {})).items()
        }
    except Exception:
        audit = {}

    return MetaSnapshot(
        regime_vote=str(getattr(decision, "regime_vote", "NEUTRAL")),
        pm_threshold=float(getattr(decision, "pm_threshold", _DEFAULT_PM_THRESHOLD)),
        enabled_setups=enabled,
        risk_budget_R=float(getattr(decision, "risk_budget_R", _DEFAULT_RISK_BUDGET_R)),
        size_multiplier=float(getattr(decision, "size_multiplier", _DEFAULT_SIZE_MULTIPLIER)),
        trade_allowed=bool(getattr(decision, "trade_allowed", True)),
        confidence=float(getattr(decision, "confidence", 0.0)),
        reason=str(getattr(decision, "reason", "")),
        voices=voices,
        audit=audit,
    )


def meta_to_firm_payload(base_payload: dict[str, Any],
                         snapshot: MetaSnapshot | None) -> dict[str, Any]:
    """Return a NEW dict with ``eta_v3_meta`` enrichment added.

    If ``snapshot`` is None the base is copied and returned unchanged
    (fail-open — the orchestrator keeps its pre-meta behaviour).

    Also sets a handful of convenience top-level keys the orchestrator
    can read without walking the nested dict, mirroring the per-trade
    adapter's ``eta_v3_pm_final`` / ``eta_v3_regime`` pattern.
    """
    if snapshot is None:
        return dict(base_payload)
    enriched = dict(base_payload)
    enriched["eta_v3_meta"] = snapshot.as_dict()
    enriched.setdefault("eta_v3_meta_trade_allowed", snapshot.trade_allowed)
    enriched.setdefault("eta_v3_meta_pm_threshold", snapshot.pm_threshold)
    enriched.setdefault("eta_v3_meta_size_multiplier", snapshot.size_multiplier)
    enriched.setdefault("eta_v3_meta_risk_budget_R", snapshot.risk_budget_R)
    enriched.setdefault("eta_v3_meta_regime_vote", snapshot.regime_vote)
    return enriched


def summarize_meta(snapshot: MetaSnapshot | None) -> str:
    """Single-line diagnostic, safe for logs and reporter scripts."""
    if snapshot is None:
        return "eta_v3_meta: unavailable"
    gate = "TRADE" if snapshot.trade_allowed else "PAUSE"
    n_setups = len(snapshot.enabled_setups)
    return (
        f"eta_v3_meta: {gate} · regime={snapshot.regime_vote} · "
        f"pm={snapshot.pm_threshold:.1f} · size_x={snapshot.size_multiplier:.2f} · "
        f"budget={snapshot.risk_budget_R:.1f}R · "
        f"setups={n_setups}/{len(_DEFAULT_ENABLED_SETUPS)} · "
        f"conf={snapshot.confidence:.0f}"
    )


def apply_meta_overrides(strategy_params: dict[str, Any],
                         snapshot: MetaSnapshot | None) -> dict[str, Any]:
    """Fold meta-decisions into a strategy params dict, non-destructively.

    The orchestrator passes its base strategy params (pm_gate, size,
    allowed_setups, daily_loss_cap_r) and gets back a new dict where
    those fields have been overridden by the meta-Firm's system-level
    verdict — unless the snapshot is None, in which case the input is
    returned unchanged.

    The caller is responsible for honouring ``trade_allowed``; when
    False the override dict still contains the per-param overrides
    (useful for telemetry) but the orchestrator should skip trading
    for the day.

    Keeps the mapping here (rather than inside firm_meta) so Meta
    stays a pure decision engine and this adapter owns the translation
    into strategy-shaped keys.
    """
    out = dict(strategy_params)
    if snapshot is None:
        return out
    out["pm_gate"] = float(snapshot.pm_threshold)
    out["size_multiplier"] = float(snapshot.size_multiplier)
    out["daily_loss_cap_r"] = float(snapshot.risk_budget_R)
    out["allowed_setups"] = list(snapshot.enabled_setups)
    # Surface trade_allowed so callers that care about the kill-switch
    # don't have to reach back into the snapshot. The override dict is
    # the single source of truth the orchestrator should consult.
    out["trade_allowed"] = bool(snapshot.trade_allowed)
    out["meta_regime_vote"] = str(snapshot.regime_vote)
    out["meta_confidence"] = float(snapshot.confidence)
    out["meta_reason"] = str(snapshot.reason)
    return out
