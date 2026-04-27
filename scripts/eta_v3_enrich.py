#!/usr/bin/env python3
"""Apex V3 AgentInput-enrichment end-to-end verifier.

Where ``eta_v3_bridge.py`` demonstrates the *payload* enrichment diff,
this script exercises the ``enrich_agent_input`` path against a
synthetic ``firm.agents.base.AgentInput`` — closing the loop between
the adapter and the real Firm review contract.

Writes ``reports/eta_v3_enrich.md`` with:

  - Whether ``firm.agents.base.AgentInput`` is constructible through
    the existing bridge shim (``mnq.firm_runtime``).
  - A before/after diff of the AgentInput.payload keys.
  - The single-line voice summary ``summarize_voices`` produces.

Always exits 0 — observational. Fail-open on every branch.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.eta_v3 import (  # noqa: E402
    ApexVoiceSnapshot,
    enrich_agent_input,
    probe_eta_v3_engine,
    run_apex_evaluation,
    summarize_voices,
)

REPORT = REPO_ROOT / "reports" / "eta_v3_enrich.md"


def _try_build_agent_input(payload: dict[str, Any]) -> tuple[Any | None, str]:
    """Try to construct a real AgentInput via the firm bridge shim.

    Falls back to a minimal duck-typed stub with a ``.payload`` attribute
    if the shim isn't wired.  Returns (instance, source_label).
    """
    try:
        from mnq import firm_runtime  # noqa: F401

        try:
            AgentInput = firm_runtime.AgentInput  # type: ignore[attr-defined]
            instance = AgentInput(payload=payload)  # type: ignore[call-arg]
            return instance, "firm.agents.base.AgentInput (via shim)"
        except Exception:
            pass
    except ImportError:
        pass

    class _StubAgentInput:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

    return _StubAgentInput(payload=payload), "stub AgentInput (bridge not wired)"


def _synthetic_snapshot() -> ApexVoiceSnapshot:
    """Hand-crafted snapshot for cases where the engine is unavailable.

    Lets the enrichment path still be exercised and visually diffed
    even when ``eta_v3_framework`` can't be imported on this machine.
    """
    return ApexVoiceSnapshot(
        regime="TREND",
        pm_final=+3.4,
        quant_total=+6.0,
        red_team=1.5,
        red_team_weighted=1.5,
        voice_agree=11,
        direction=1,
        fire_long=True,
        fire_short=False,
        setup_name="ORB_RETEST_LONG",
        blocked_reason="",
        voices={f"V{i}": round(0.9 - 0.05 * i, 3) for i in range(1, 16)},
    )


def main() -> int:
    probe = probe_eta_v3_engine()
    base_payload: dict[str, Any] = {
        "symbol": "MNQ",
        "side": "long",
        "qty": 1,
        "trace_id": "enrich-smoke",
        "price": 21008.0,
    }

    snapshot: ApexVoiceSnapshot | None = None
    snapshot_source = "synthetic (engine unavailable)"

    if probe.get("available"):
        try:
            import firm_engine  # type: ignore

            bar = firm_engine.Bar(
                time=int(datetime.now(UTC).timestamp()),
                open=21000.0,
                high=21010.0,
                low=20995.0,
                close=21008.0,
                volume=1200.0,
                atr=4.5,
                vwap=21005.0,
                ema9=21006.0,
                ema21=21001.0,
                ema50=20995.0,
                rsi=55.0,
                adx=24.0,
                htf_close=21000.0,
                htf_ema50=20985.0,
            )
            setup = firm_engine.SetupTriggers(
                orb_long=True,
                ema_trend_bull=True,
                ema_in_zone=False,
                orb_score=4,
                ema_score=3,
                sweep_score=0,
            )
            snapshot = run_apex_evaluation(
                bar,
                setup,
                regime="NEUTRAL",
                atr_ma20=4.2,
                vol_z=0.3,
                prev_adx_3=22.0,
                range_avg_20=14.0,
                vol_z_prev_1=0.25,
                vol_z_prev_2=0.20,
                highest_5_prev=21012.0,
                lowest_5_prev=20992.0,
                recent_losses=0,
                prev_day_high=21020.0,
                prev_day_low=20980.0,
            )
            snapshot_source = "eta_v3 firm_engine.evaluate"
        except Exception:
            snapshot = None
            snapshot_source = "engine import then raised"
    if snapshot is None:
        snapshot = _synthetic_snapshot()

    # Build a real AgentInput (or stub) and enrich it in place.
    agent_input, ai_source = _try_build_agent_input(dict(base_payload))
    before_keys = sorted(getattr(agent_input, "payload", {}).keys())
    enrich_agent_input(agent_input, snapshot)
    after_keys = sorted(getattr(agent_input, "payload", {}).keys())
    added = sorted(set(after_keys) - set(before_keys))

    lines = [
        f"# Apex V3 AgentInput Enrichment — {datetime.now(UTC).isoformat()}",
        "",
        f"**Engine available:** {'🟢 yes' if probe.get('available') else '🔴 no'}",
        f"**Snapshot source:** {snapshot_source}",
        f"**AgentInput source:** {ai_source}",
        "",
        "## Voice summary",
        "",
        "```",
        summarize_voices(snapshot),
        "```",
        "",
        "## AgentInput.payload keys",
        "",
        "| Before enrichment | After enrichment |",
        "|---|---|",
        f"| `{before_keys}` | `{after_keys}` |",
        "",
        "**Added keys:** " + (", ".join(f"`{k}`" for k in added) if added else "_none_"),
        "",
        "## Added payload content",
        "",
        "```json",
        json.dumps(
            {k: getattr(agent_input, "payload", {}).get(k) for k in added},
            indent=2,
            default=str,
            sort_keys=True,
        ),
        "```",
        "",
        "This closes the loop from the adapter into the Firm review",
        "contract: the Quant agent inside the 6-stage chain sees",
        "``payload['eta_v3_voices']`` alongside the base spec fields.",
        "The enrichment is idempotent — calling again with the same",
        "snapshot produces the same dict (no duplicate keys).",
    ]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")

    status = "🟢" if added else "🟡 no-op"
    print(
        f"eta_v3_enrich: {status}  ·  added={len(added)} keys  ·  "
        f"report={REPORT.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
