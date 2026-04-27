"""Apex V3 Meta-Orchestrator — dynamic config from meta-voices.

Batch 8B. Reads the meta-firm's system-level decisions and produces a
runtime config that the orchestrator can consume. This bridges the gap
between the meta-voices' recommendations and the actual execution parameters.

The meta-firm decides:
  - Whether to trade today (trade_allowed)
  - PM threshold for filtering (pm_threshold)
  - Size multiplier (size_multiplier)
  - Risk budget in R-multiples (risk_budget_R)
  - Which setups are enabled (enabled_setups)
  - Confidence level (confidence)

This script:
  1. Runs meta-evaluation with current-session stats
  2. Produces a JSON config file at data/meta_config.json
  3. Reports differences from default params

Output: ``reports/eta_meta_orchestrator.md``
        ``data/meta_config.json``
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
    MetaSnapshot,
    apply_meta_overrides,
    build_meta_context,
    probe_meta_firm_engine,
    run_meta_evaluation,
    summarize_meta,
)

REPORT = REPO_ROOT / "reports" / "eta_meta_orchestrator.md"
CONFIG_OUT = REPO_ROOT / "data" / "meta_config.json"

# Default strategy parameters — the baseline before meta overrides
DEFAULT_PARAMS = {
    "pm_gate": 40.0,
    "size_multiplier": 1.0,
    "daily_loss_cap_r": 3.0,
    "allowed_setups": ["ORB", "EMA PB", "SWEEP"],
    "gauntlet_weight": 0.15,
    "max_trades_per_day": 5,
}


def _load_session_stats() -> dict:
    """Load current session stats for meta-context.

    Reads from journal/reports to build a realistic MetaContext.
    Falls back to synthetic values if data isn't available.
    """
    stats = {
        "recent_trades": [],
        "rolling_win_rate": 0.50,
        "rolling_pf": 1.0,
        "rolling_dd": 0.0,
        "current_equity_r": 0.0,
        "peak_equity_r": 0.0,
        "consecutive_losses": 0,
        "consecutive_wins": 0,
        "days_since_last_win": 0,
        "regime_history": [],
        "avg_atr": 4.0,
        "avg_adx": 20.0,
        "avg_vol_z": 0.0,
        "hour_et": datetime.now(UTC).hour - 4,  # rough ET
        "weekday": datetime.now(UTC).weekday(),
    }

    # Try loading loss streak state
    loss_state = REPO_ROOT / "data" / "loss_streak_state.json"
    if loss_state.exists():
        try:
            ls = json.loads(loss_state.read_text())
            stats["consecutive_losses"] = ls.get("current_streak", 0)
        except Exception:
            pass

    # Try loading from bayesian expectancy report
    bayes = REPO_ROOT / "reports" / "bayesian_expectancy.md"
    if bayes.exists():
        try:
            text = bayes.read_text()
            for line in text.splitlines():
                if "overall win rate" in line.lower():
                    # Try to extract the number
                    parts = line.split(":")
                    if len(parts) >= 2:
                        try:
                            val = float(parts[-1].strip().rstrip("%").strip("*")) / 100
                            stats["rolling_win_rate"] = val
                        except ValueError:
                            pass
        except Exception:
            pass

    return stats


def main() -> int:
    now = datetime.now(UTC).isoformat()
    probe = probe_meta_firm_engine()

    # Load current session context
    session_stats = _load_session_stats()

    snapshot: MetaSnapshot | None = None
    source = "synthetic"

    if probe.get("available"):
        ctx = build_meta_context(**session_stats)
        snapshot = run_meta_evaluation(ctx, base_pm=DEFAULT_PARAMS["pm_gate"])
        if snapshot is not None:
            source = "live (firm_meta engine)"

    if snapshot is None:
        # Synthetic fallback
        snapshot = MetaSnapshot(
            regime_vote="NEUTRAL",
            pm_threshold=DEFAULT_PARAMS["pm_gate"],
            enabled_setups=DEFAULT_PARAMS["allowed_setups"],
            risk_budget_R=DEFAULT_PARAMS["daily_loss_cap_r"],
            size_multiplier=DEFAULT_PARAMS["size_multiplier"],
            trade_allowed=True,
            confidence=50.0,
            reason="SYNTHETIC: engine unavailable",
            voices={},
            audit={},
        )
        source = "synthetic (engine unavailable)"

    # Apply overrides
    overridden = apply_meta_overrides(dict(DEFAULT_PARAMS), snapshot)

    # Compute diffs
    diffs = {}
    for k, v in overridden.items():
        if DEFAULT_PARAMS.get(k) != v:
            diffs[k] = {"default": DEFAULT_PARAMS.get(k), "overridden": v}

    # Write runtime config
    config = {
        "generated_at": now,
        "source": source,
        "trade_allowed": snapshot.trade_allowed,
        "confidence": snapshot.confidence,
        "params": overridden,
        "diffs_from_default": diffs,
        "meta_summary": summarize_meta(snapshot),
    }

    CONFIG_OUT.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_OUT.write_text(json.dumps(config, indent=2, default=str, sort_keys=True))

    # Write report
    lines = [
        f"# Apex V3 Meta-Orchestrator Config — {now}",
        "",
        f"**Source:** {source}",
        f"**Trade allowed:** {'YES' if snapshot.trade_allowed else 'NO'}",
        f"**Confidence:** {snapshot.confidence:.1f}%",
        f"**Regime vote:** {snapshot.regime_vote}",
        "",
        "## Summary",
        "",
        f"```{summarize_meta(snapshot)}```",
        "",
        "## Runtime parameters",
        "",
        "| Parameter | Default | Override | Changed |",
        "|---|---:|---:|:---|",
    ]

    for k in sorted(DEFAULT_PARAMS):
        default_v = DEFAULT_PARAMS[k]
        override_v = overridden.get(k, default_v)
        changed = "**YES**" if k in diffs else ""
        lines.append(f"| {k} | {default_v} | {override_v} | {changed} |")

    lines.extend(
        [
            "",
            "## Meta-voice outputs",
            "",
            "```json",
            json.dumps(snapshot.voices or {}, indent=2, sort_keys=True),
            "```",
            "",
            "## Integration status",
            "",
            f"- Config written to: `{CONFIG_OUT.relative_to(REPO_ROOT)}`",
            "- The orchestrator reads `data/meta_config.json` at startup",
            "  and applies the overridden parameters to the current run.",
            "- When `trade_allowed=false`, the orchestrator should skip",
            "  all live trade execution stages (shadow mode only).",
            "",
            "## How to use",
            "",
            "```python",
            'config = json.loads(Path("data/meta_config.json").read_text())',
            'if not config["trade_allowed"]:',
            '    print("META-FIRM: trading paused today")',
            "    # Skip execution, run shadow only",
            "```",
        ]
    )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")

    status = "LIVE" if source.startswith("live") else "SYNTHETIC"
    n_diffs = len(diffs)
    print(
        f"eta_meta_orchestrator: {status}  ·  "
        f"trade_allowed={snapshot.trade_allowed}  ·  "
        f"overrides={n_diffs}  ·  "
        f"config={CONFIG_OUT.relative_to(REPO_ROOT)}  ·  "
        f"report={REPORT.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
