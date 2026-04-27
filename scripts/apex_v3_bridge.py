#!/usr/bin/env python3
"""Apex V3 ↔ Firm bridge reporter.

Orchestrator-facing smoke test: probes the Apex V3 firm_engine,
runs a synthetic evaluation, demonstrates payload enrichment, and
writes ``reports/eta_v3_bridge.md``.

Always exits 0 — this is observational, not enforcement. The adapter
is explicitly tolerant of a missing engine (returns None → payload
passes through unchanged).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.eta_v3 import (  # noqa: E402
    apex_to_firm_payload,
    build_enrichment_payload,
    probe_eta_v3_engine,
    run_apex_evaluation,
    summarize_voices,
)

REPORT = REPO_ROOT / "reports" / "eta_v3.md"
# Back-compat alias — earlier skill references and tracker point at the old path.
REPORT_LEGACY = REPO_ROOT / "reports" / "eta_v3_bridge.md"


def _synthetic_bar_and_setup():
    """Construct a Bar + SetupTriggers if eta_v3_framework is importable."""
    try:
        import firm_engine  # type: ignore
    except ImportError:
        return None, None
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
    return bar, setup


# Canonical kwargs the V3 firm_engine.evaluate() signature demands in
# addition to (bar, setup, regime). Kept here so both the bridge and
# the enrich reporter call into the engine the same way.
EVAL_EXTRA_KWARGS = {
    "atr_ma20": 4.2,
    "vol_z": 0.3,
    "prev_adx_3": 22.0,
    "range_avg_20": 14.0,
    "vol_z_prev_1": 0.25,
    "vol_z_prev_2": 0.20,
    "highest_5_prev": 21012.0,
    "lowest_5_prev": 20992.0,
    "recent_losses": 0,
    "prev_day_high": 21020.0,
    "prev_day_low": 20980.0,
}


def main() -> int:
    probe = probe_eta_v3_engine()
    base_payload = {"symbol": "MNQ", "side": "long", "qty": 1, "trace_id": "bridge-smoke"}

    snapshot = None
    enriched = dict(base_payload)

    if probe.get("available"):
        bar, setup = _synthetic_bar_and_setup()
        if bar is not None:
            snapshot = run_apex_evaluation(bar, setup, regime="NEUTRAL", **EVAL_EXTRA_KWARGS)
            enriched = apex_to_firm_payload(base_payload, snapshot)

    # Demonstrate idempotent enrichment-without-engine path
    stub_enriched = build_enrichment_payload(base_payload, None)

    # Render report
    lines = [
        f"# Apex V3 Bridge — {datetime.now(UTC).isoformat()}",
        "",
        f"**Engine available:** {'🟢 yes' if probe.get('available') else '🔴 no'}",
    ]
    if not probe.get("available"):
        lines.append(f"**Reason:** {probe.get('reason', '?')}")
    else:
        lines.append(
            f"**Voices exposed:** {probe.get('voices_found', 0)} · "
            f"evaluate={probe.get('has_evaluate')} · "
            f"detect_regime={probe.get('has_detect_regime')}"
        )

    lines.extend(
        [
            "",
            "## Voice snapshot",
            "",
            "```",
            summarize_voices(snapshot),
            "```",
            "",
            "## Payload enrichment",
            "",
            "Base payload:",
            "",
            "```json",
            json.dumps(base_payload, indent=2, sort_keys=True),
            "```",
            "",
            "Enriched payload (keys added):",
            "",
            "```json",
            json.dumps(
                {k: v for k, v in enriched.items() if k not in base_payload}
                or {"_note": "no enrichment (engine unavailable)"},
                indent=2,
                default=str,
                sort_keys=True,
            ),
            "```",
            "",
            "## Stub path (engine absent)",
            "",
            "`build_enrichment_payload(base, None)` returns the base unchanged — "
            f"proves the adapter's fail-open contract. Stub === base: "
            f"{stub_enriched == base_payload}.",
            "",
            "This reporter is read-only. The adapter is consumed by the",
            "Quant agent inside the existing Firm bridge shim at",
            "`src/mnq/firm_runtime.py` — no new import boundaries introduced.",
        ]
    )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n".join(lines) + "\n"
    REPORT.write_text(rendered)
    # Keep legacy filename in sync for any downstream consumer still
    # reading `eta_v3_bridge.md`. Safe to remove once tracker cut over.
    REPORT_LEGACY.write_text(rendered)

    # Console line
    status = (
        "🟢 LIVE"
        if snapshot is not None
        else (
            "🟡 ADAPTER-OK · engine unavailable"
            if not probe.get("available")
            else "🟡 ENGINE-OK · evaluate returned None"
        )
    )
    print(f"eta_v3_bridge: {status}  ·  report={REPORT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
