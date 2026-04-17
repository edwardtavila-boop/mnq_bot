#!/usr/bin/env python3
"""Apex V3 Meta-Firm reporter — system-level decision smoke test.

Companion to the three per-trade Apex V3 reporters. Where those
exercise the 15-voice ``firm_engine.evaluate`` path, this one
exercises ``firm_meta.run_meta_firm`` — the system-level layer that
decides today's PM threshold, size multiplier, enabled setups, and
whether to trade at all.

Writes ``reports/eta_v3_meta.md`` with:

  - Whether ``firm_meta`` is importable.
  - A synthetic MetaContext (hand-crafted rolling stats) run through
    ``run_meta_evaluation``.
  - The single-line summary ``summarize_meta`` produces.
  - The payload fragment ``meta_to_firm_payload`` emits.
  - The strategy-param overrides ``apply_meta_overrides`` produces.

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
    MetaSnapshot,
    apply_meta_overrides,
    build_meta_context,
    meta_to_firm_payload,
    probe_meta_firm_engine,
    run_meta_evaluation,
    summarize_meta,
)

REPORT = REPO_ROOT / "reports" / "eta_v3_meta.md"


def _synthetic_context_kwargs() -> dict[str, Any]:
    """Hand-crafted MetaContext fields.

    Shape mirrors firm_meta.MetaContext. Values chosen to exercise a
    range of meta-voices without tripping the hard pause.
    """
    return {
        "recent_trades": [{"pnl": 1.2}, {"pnl": -0.5}, {"pnl": 0.8}],
        "rolling_win_rate": 0.58,
        "rolling_pf": 1.75,
        "rolling_dd": 0.8,
        "current_equity_r": 4.5,
        "peak_equity_r": 5.0,
        "consecutive_losses": 1,
        "consecutive_wins": 0,
        "days_since_last_win": 1,
        "regime_history": ["NEUTRAL"] * 6 + ["RISK-ON"] * 4,
        "avg_atr": 4.5,
        "avg_adx": 22.0,
        "avg_vol_z": 0.4,
        "hour_et": 10,
        "weekday": 2,  # Tuesday
    }


def _synthetic_snapshot() -> MetaSnapshot:
    """Fallback snapshot used when firm_meta isn't importable.

    Lets the enrichment path still be exercised and visually diffed on
    machines without the eta_v3_framework package installed.
    """
    return MetaSnapshot(
        regime_vote="NEUTRAL",
        pm_threshold=32.0,
        enabled_setups=["ORB", "EMA PB", "SWEEP"],
        risk_budget_R=2.0,
        size_multiplier=1.0,
        trade_allowed=True,
        confidence=55.0,
        reason="SYNTHETIC: engine unavailable, default verdict",
        voices={"regime_stability": 40.0, "time_of_day": 60.0, "day_of_week": 50.0},
        audit={"regime_vote": "synthetic fallback"},
    )


def main() -> int:
    probe = probe_meta_firm_engine()
    now = datetime.now(UTC).isoformat()

    snapshot: MetaSnapshot | None = None
    source_label = "synthetic (engine unavailable)"

    if probe.get("available"):
        ctx = build_meta_context(**_synthetic_context_kwargs())
        snapshot = run_meta_evaluation(ctx, base_pm=30.0)
        source_label = (
            "eta_v3 firm_meta.run_meta_firm"
            if snapshot is not None
            else "engine importable but evaluation returned None"
        )
    if snapshot is None:
        snapshot = _synthetic_snapshot()

    base_payload = {
        "symbol": "MNQ",
        "side": "long",
        "qty": 1,
        "trace_id": "meta-smoke",
    }
    enriched = meta_to_firm_payload(base_payload, snapshot)
    added = sorted(set(enriched) - set(base_payload))

    base_strategy_params = {
        "pm_gate": 40.0,
        "size_multiplier": 1.0,
        "daily_loss_cap_r": 3.0,
        "allowed_setups": ["ORB", "EMA PB", "SWEEP"],
    }
    overridden = apply_meta_overrides(base_strategy_params, snapshot)
    strat_diff = sorted(
        k for k, v in overridden.items() if base_strategy_params.get(k) != v
    )

    lines: list[str] = [
        f"# Apex V3 Meta-Firm — {now}",
        "",
        f"**Engine available:** {'yes' if probe.get('available') else 'no'}",
        f"**Snapshot source:** {source_label}",
        "",
    ]
    if probe.get("available"):
        lines.append(
            f"- Meta-voices exposed: **{probe.get('voices_found', 0)}**"
        )
        lines.append(
            f"- `run_meta_firm` callable: **{probe.get('has_run_meta_firm')}**"
        )
        lines.append(
            f"- `MetaContext` dataclass: **{probe.get('has_meta_context')}**"
        )
        lines.append("")
        lines.append("## Meta-voice names")
        lines.append("")
        lines.append("```")
        lines.append("\n".join(probe.get("voice_names", []) or ["<none>"]))
        lines.append("```")
        lines.append("")
    else:
        lines.append(f"- Reason: `{probe.get('reason', 'unknown')}`")
        lines.append("")
        lines.append(
            "The adapter's contract is fail-open: with the engine unavailable,"
        )
        lines.append(
            "``meta_to_firm_payload(base, None)`` returns the base unchanged."
        )
        lines.append("")

    lines.extend([
        "## Single-line summary",
        "",
        "```",
        summarize_meta(snapshot),
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
        "Enriched keys (`eta_v3_meta*`):",
        "",
        "```json",
        json.dumps(
            {k: enriched[k] for k in added} or {"_note": "no enrichment"},
            indent=2, default=str, sort_keys=True,
        ),
        "```",
        "",
        "## Strategy-param overrides",
        "",
        "Base params:",
        "",
        "```json",
        json.dumps(base_strategy_params, indent=2, sort_keys=True),
        "```",
        "",
        "Overridden params (changed vs base):",
        "",
        "```json",
        json.dumps(
            {k: overridden[k] for k in strat_diff}
            or {"_note": "no overrides (engine unavailable)"},
            indent=2, default=str, sort_keys=True,
        ),
        "```",
        "",
        "## Full MetaSnapshot",
        "",
        "```json",
        json.dumps(snapshot.as_dict(), indent=2, default=str, sort_keys=True),
        "```",
        "",
        "This reporter is read-only. The overrides surface in",
        "`scripts/firm_live_review.py` as an additional payload fragment",
        "and, where the orchestrator honours them, a per-run override",
        "of PM gate, size multiplier, and daily loss cap.",
    ])

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    # Force UTF-8 encoding — Windows' locale default is cp1252, which
    # renders the em-dash and mid-dot but not the emojis the sister
    # reporters use. Keeping the output normalised to UTF-8 makes
    # the whole Phase F bundle consistent regardless of PYTHONUTF8.
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    status = "LIVE" if probe.get("available") and snapshot is not None else "FAIL-OPEN"
    print(
        f"eta_v3_meta: {status}  ·  "
        f"trade_allowed={snapshot.trade_allowed}  ·  "
        f"pm={snapshot.pm_threshold:.1f}  ·  "
        f"size_x={snapshot.size_multiplier:.2f}  ·  "
        f"report={REPORT.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
