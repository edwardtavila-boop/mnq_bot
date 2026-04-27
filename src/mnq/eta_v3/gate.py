"""Apex V3 downstream gate.

Batches 3A/3B wired `eta_v3_voices` + `eta_v3_pm_final` into the Firm's
Quant and PM agents. Both agents now emit an `eta_v3` block in their
output payload containing `base_probability`, `adjusted_probability`, and
`delta`. Batch 3C locked the verdict-preservation contract at integration
level.

Batch 3D (this module) turns that *probability delta* into an actual
trade-level filter. The gate is intentionally a **pure function**: it
takes a PM output dict (the same dict `run_six_stage_review` produces)
and emits a routing decision:

    {"action": "full" | "reduced" | "skip", "size_mult": float, "reason": str}

- ``full``    → ship the trade at full risk (size_mult=1.0)
- ``reduced`` → ship at half risk (size_mult=0.5)
- ``skip``    → do not ship the trade (size_mult=0.0)

### Thresholds

Delta is `adjusted_probability - base_probability`. Positive delta =
Apex corroborates. Negative delta = Apex dissents.

- ``delta >= +0.02`` → full size (Apex actively corroborates)
- ``-0.05 < delta < +0.02`` → full size when PM verdict is GO, reduced
  when PM verdict is MODIFY (soft-ambivalent)
- ``-0.10 <= delta <= -0.05`` → reduced size (Apex mildly dissents)
- ``delta < -0.10`` → skip (Apex strongly dissents)

### Fail-safe

If the PM output has no ``eta_v3`` block (e.g., Apex engine was offline
or the payload never carried voices), the gate returns ``full`` with
reason="no_apex_signal". This preserves the no-op contract: if Apex is
unavailable, the system falls back to whatever the pre-Apex Firm gauntlet
would have done.

Any PM verdict that is not GO/MODIFY forces ``skip`` regardless of delta
— the gate never overrides a KILL or HOLD.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "GateAction",
    "GateDecision",
    "apex_gate",
]


# --- constants ----------------------------------------------------------------

DELTA_STRONG_DISSENT = -0.10  # below this → skip
DELTA_SOFT_DISSENT = -0.05  # below this (and >= STRONG) → reduced
DELTA_ACTIVE_CORROBORATE = 0.02  # above this → full

SIZE_FULL = 1.0
SIZE_REDUCED = 0.5
SIZE_SKIP = 0.0

GO_LIKE = frozenset({"GO", "MODIFY"})


# --- public API ---------------------------------------------------------------


class GateAction:
    """String constants for gate actions (simpler than an enum for JSON)."""

    FULL = "full"
    REDUCED = "reduced"
    SKIP = "skip"


GateDecision = dict[str, Any]  # {"action": str, "size_mult": float, "reason": str}


def apex_gate(pm_output: dict[str, Any] | None) -> GateDecision:
    """Route a PM output through the Apex delta gate.

    Parameters
    ----------
    pm_output:
        The ``outputs['pm']`` dict returned by ``run_six_stage_review``.
        Expected keys: ``verdict`` (str) and ``payload.eta_v3`` (dict
        with ``delta``, ``base_probability``, ``adjusted_probability``,
        ``consumed``). Missing/malformed blocks are handled defensively.

    Returns
    -------
    GateDecision:
        {"action": "full|reduced|skip", "size_mult": float, "reason": str}
    """
    # Defensive: empty/None input → fail-safe skip (can't ship a trade we can't verify)
    if not isinstance(pm_output, dict) or not pm_output:
        return _decision(
            GateAction.SKIP,
            SIZE_SKIP,
            "pm_output_missing_or_invalid",
        )

    verdict = str(pm_output.get("verdict", "")).upper()

    # PM authority is absolute: only GO/MODIFY can ship at all.
    if verdict not in GO_LIKE:
        return _decision(
            GateAction.SKIP,
            SIZE_SKIP,
            f"pm_verdict_{verdict.lower() or 'unknown'}_blocks_ship",
        )

    # Locate the Apex summary
    payload = pm_output.get("payload") or {}
    apex = payload.get("eta_v3") if isinstance(payload, dict) else None

    # No Apex block or engine didn't consume → fall back to pre-Apex behavior.
    if not isinstance(apex, dict) or not apex.get("consumed"):
        return _decision(
            GateAction.FULL,
            SIZE_FULL,
            "no_apex_signal_falling_back",
        )

    delta = _safe_float(apex.get("delta"), 0.0)

    # Strong dissent → skip entirely
    if delta < DELTA_STRONG_DISSENT:
        return _decision(
            GateAction.SKIP,
            SIZE_SKIP,
            f"apex_strong_dissent_delta={delta:+.3f}",
        )

    # Soft dissent → half size
    if delta < DELTA_SOFT_DISSENT:
        # already filtered: DELTA_STRONG_DISSENT <= delta < DELTA_SOFT_DISSENT
        return _decision(
            GateAction.REDUCED,
            SIZE_REDUCED,
            f"apex_soft_dissent_delta={delta:+.3f}",
        )

    # Active corroboration → full
    if delta >= DELTA_ACTIVE_CORROBORATE:
        return _decision(
            GateAction.FULL,
            SIZE_FULL,
            f"apex_corroborates_delta={delta:+.3f}",
        )

    # Neutral zone: -0.05 <= delta < 0.02
    # MODIFY is already a reduced-conviction verdict — trim further under neutral Apex.
    if verdict == "MODIFY":
        return _decision(
            GateAction.REDUCED,
            SIZE_REDUCED,
            f"apex_neutral_on_modify_delta={delta:+.3f}",
        )

    # Clean GO with neutral Apex → ship full
    return _decision(
        GateAction.FULL,
        SIZE_FULL,
        f"apex_neutral_on_go_delta={delta:+.3f}",
    )


# --- helpers ------------------------------------------------------------------


def _decision(action: str, size_mult: float, reason: str) -> GateDecision:
    return {"action": action, "size_mult": float(size_mult), "reason": reason}


def _safe_float(x: Any, default: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    # NaN guard
    if v != v:
        return default
    return v
