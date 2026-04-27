"""End-to-end phase orchestrator.

Turns the bot on and runs the full roadmap pipeline in one shot:

    Phase 0 — Verify Integration      live_sim (real-data internal run)
    Phase 1 — Harden Foundation       crash_recovery_test
    Phase 2 — Event Log & Replay      replay_journal
    Phase 3 — Fill Documented Gaps    calibration, firm_vs_baseline,
                                      firm_review (markdown),
                                      firm_live_review (Firm Python agents),
                                      postmortem
    Phase 4 — Backtest/Live Parity    built-in to replay_journal checksum
    Phase 5 — Advanced Risk           bayesian_expectancy
    Cross   — Registry + digest       strategy_registry, walk_forward,
                                      daily_digest
    Phase A — Quick-win reporters     alerting, edge_decay, mae_mfe,
                                      time_heatmap, counterfactual,
                                      trade_governor, rule_adherence,
                                      email_recap, time_exit
    Phase B — Psych + review          psych_sidecar, pre_trade_pause,
                                      loss_streak_monitor, hot_hand_detector,
                                      auto_screenshot, voice_memo,
                                      weekly_review, monthly_narrative,
                                      mistake_taxonomy, ai_reviewer
    Phase C — Market context          cumulative_delta, volume_profile,
                                      sector_rotation, news_feed,
                                      gex_monitor, vix_term, breadth_monitor,
                                      event_calendar, earnings_amp,
                                      seasonality
    Phase D — ML + resilience + tax   gbm_filter, shap_rank, trade_clusters,
                                      anomaly_detect, heartbeat,
                                      deadman_switch, encrypted_backup,
                                      tax_1256, pretrade_checklist,
                                      correlation_cap
    Phase E — Enforcement spine       gate_chain_check, parity_harness,
                                      gauntlet_check

    Phase 12 — Real-tape backtest    backtest_real, backtest_real_analysis

The orchestrator does NOT stop on a single stage failure; it records the
pass/fail, moves on, and writes ``reports/run_all_phases.md`` with the
aggregate status so you can see at a glance which phases held green.

Usage:

    python scripts/run_all_phases.py
    python scripts/run_all_phases.py --skip crash_recovery  # skip heavy stage
    python scripts/run_all_phases.py --only live_sim firm_live_review
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

UTC = timezone.utc  # noqa: UP017
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "run_all_phases.md"


# Resolve python: prefer venv if the interpreter is executable on this OS,
# otherwise fall back to the currently-running interpreter. Windows venv lives
# under Scripts/, Linux under bin/ — honor both.
def _resolve_python() -> str:
    import os
    import platform

    # On Linux, a Windows .exe passes os.access but fails to exec with
    # "Exec format error". Skip Scripts/ paths unless we're actually on
    # Windows so Linux CI and the sandbox use the right interpreter.
    is_windows = platform.system() == "Windows"
    candidates = []
    if is_windows:
        candidates.append(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
    candidates.extend(
        [
            REPO_ROOT / ".venv" / "bin" / "python",
            REPO_ROOT / ".venv" / "bin" / "python3",
        ]
    )
    for c in candidates:
        if c.exists() and os.access(str(c), os.X_OK):
            return str(c)
    return sys.executable


PYTHON = _resolve_python()


# Windows consoles default to cp1252 which chokes on the ✓/×/→ symbols
# used in progress prints. Force UTF-8 so the orchestrator never dies on
# a print. Safe no-op on POSIX where stdout is already UTF-8.
import contextlib  # noqa: E402 -- intentionally placed after sys.stdout setup

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@dataclass
class Stage:
    name: str
    phase: str
    cmd: list[str]
    blocking: bool = False  # if True, a failure stops the run


@dataclass
class StageResult:
    name: str
    phase: str
    returncode: int
    duration_s: float
    stdout_tail: str
    stderr_tail: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# Phase A-D: all run after the core pipeline. Non-blocking — these are
# reporters and gates that shouldn't take down the run if a feed's offline.
_PHASE_A_STAGES: list[Stage] = [
    Stage("alerting", "Phase A", [PYTHON, "scripts/alerting.py", "--tier", "info"]),
    Stage("edge_decay", "Phase A", [PYTHON, "scripts/edge_decay.py"]),
    Stage("mae_mfe", "Phase A", [PYTHON, "scripts/mae_mfe.py"]),
    Stage("time_heatmap", "Phase A", [PYTHON, "scripts/time_heatmap.py"]),
    Stage("counterfactual", "Phase A", [PYTHON, "scripts/counterfactual.py"]),
    # --advisory: full-sweep runs use the replay journal whose trade count
    # will always trip the 8-trades/day live cap. Preserve the HOLD signal
    # in the report but don't fail the sweep; a live deployment calls
    # trade_governor.py directly without --advisory.
    Stage("trade_governor", "Phase A", [PYTHON, "scripts/trade_governor.py", "--advisory"]),
    Stage("rule_adherence", "Phase A", [PYTHON, "scripts/rule_adherence.py"]),
    Stage("email_recap", "Phase A", [PYTHON, "scripts/email_recap.py"]),
    Stage("time_exit", "Phase A", [PYTHON, "scripts/time_exit.py"]),
]

_PHASE_B_STAGES: list[Stage] = [
    Stage("psych_sidecar", "Phase B", [PYTHON, "scripts/psych_sidecar.py", "--report"]),
    Stage("pre_trade_pause", "Phase B", [PYTHON, "scripts/pre_trade_pause.py"]),
    Stage("loss_streak", "Phase B", [PYTHON, "scripts/loss_streak_monitor.py"]),
    Stage("hot_hand", "Phase B", [PYTHON, "scripts/hot_hand_detector.py"]),
    Stage("auto_screenshot", "Phase B", [PYTHON, "scripts/auto_screenshot.py", "--last", "5"]),
    Stage("voice_memo_list", "Phase B", [PYTHON, "scripts/voice_memo.py", "--list"]),
    Stage("weekly_review", "Phase B", [PYTHON, "scripts/weekly_review.py"]),
    Stage("monthly_narrative", "Phase B", [PYTHON, "scripts/monthly_narrative.py"]),
    Stage("mistake_taxonomy", "Phase B", [PYTHON, "scripts/mistake_taxonomy.py"]),
    Stage("ai_reviewer", "Phase B", [PYTHON, "scripts/ai_reviewer.py", "--last", "10"]),
]

_PHASE_C_STAGES: list[Stage] = [
    Stage("cumulative_delta", "Phase C", [PYTHON, "scripts/cumulative_delta.py"]),
    Stage("volume_profile", "Phase C", [PYTHON, "scripts/volume_profile.py"]),
    Stage("sector_rotation", "Phase C", [PYTHON, "scripts/sector_rotation.py"]),
    Stage("news_feed", "Phase C", [PYTHON, "scripts/news_feed.py"]),
    Stage("gex_monitor", "Phase C", [PYTHON, "scripts/gex_monitor.py"]),
    Stage("vix_term", "Phase C", [PYTHON, "scripts/vix_term.py"]),
    Stage("breadth_monitor", "Phase C", [PYTHON, "scripts/breadth_monitor.py"]),
    Stage("event_calendar", "Phase C", [PYTHON, "scripts/event_calendar.py"]),
    Stage("earnings_amp", "Phase C", [PYTHON, "scripts/earnings_amp.py"]),
    Stage("seasonality", "Phase C", [PYTHON, "scripts/seasonality.py"]),
]

_PHASE_D_STAGES: list[Stage] = [
    Stage("gbm_filter", "Phase D", [PYTHON, "scripts/gbm_filter.py"]),
    Stage("shap_rank", "Phase D", [PYTHON, "scripts/shap_rank.py"]),
    Stage("trade_clusters", "Phase D", [PYTHON, "scripts/trade_clusters.py"]),
    Stage("anomaly_detect", "Phase D", [PYTHON, "scripts/anomaly_detect.py"]),
    Stage("heartbeat", "Phase D", [PYTHON, "scripts/heartbeat.py", "--beat"]),
    Stage("deadman_switch", "Phase D", [PYTHON, "scripts/deadman_switch.py"]),
    Stage("encrypted_backup", "Phase D", [PYTHON, "scripts/encrypted_backup.py"]),
    Stage("tax_1256", "Phase D", [PYTHON, "scripts/tax_1256.py"]),
    Stage(
        "pretrade_checklist", "Phase D", [PYTHON, "scripts/pretrade_checklist.py", "--skip-speak"]
    ),
    Stage("correlation_cap", "Phase D", [PYTHON, "scripts/correlation_cap.py"]),
]

# Phase E: enforcement spine — gate chain evaluation, parity harness,
# and the 12-gate gauntlet. These close the loop from Phase A-D reporters
# back into actual executor enforcement (Option 1/2/3 from the roadmap).
# backtest_baseline_export precedes parity_harness so the harness has a
# real comparison target rather than the stub-PASS fallback.
_PHASE_E_STAGES: list[Stage] = [
    Stage("gate_chain_check", "Phase E", [PYTHON, "scripts/gate_chain_check.py"]),
    Stage("backtest_baseline_export", "Phase E", [PYTHON, "scripts/backtest_baseline_export.py"]),
    Stage("parity_harness", "Phase E", [PYTHON, "scripts/parity_harness.py"]),
    Stage("gauntlet_check", "Phase E", [PYTHON, "scripts/gauntlet_check.py"]),
]

# Phase F: closing reporters — 72h burn-in (compressed) and Apex V3
# four-stage enrichment chain:
#
#   eta_v3_probe   — fast import/availability check (per-trade engine)
#   eta_v3_enrich  — AgentInput end-to-end enrichment verifier
#   eta_v3_bridge  — full bridge smoke test with synthetic evaluate()
#   eta_v3_meta    — Meta-Firm (system-level) smoke test: regime vote,
#                     PM threshold, size multiplier, daily risk budget,
#                     trade-allowed kill switch
#
# All observational; never block the run.
_PHASE_F_STAGES: list[Stage] = [
    Stage("burn_in_72h", "Phase F", [PYTHON, "scripts/burn_in_72h.py", "--compression", "4800"]),
    Stage("eta_v3_probe", "Phase F", [PYTHON, "scripts/eta_v3_probe.py"]),
    Stage("eta_v3_enrich", "Phase F", [PYTHON, "scripts/eta_v3_enrich.py"]),
    Stage("eta_v3_bridge", "Phase F", [PYTHON, "scripts/eta_v3_bridge.py"]),
    Stage("eta_v3_meta", "Phase F", [PYTHON, "scripts/eta_v3_meta.py"]),
    Stage("eta_meta_orchestrator", "Phase F", [PYTHON, "scripts/eta_meta_orchestrator.py"]),
]

# Phase 12 — Real-tape backtest.
# Runs V2 strategy (all real-data variants) against the full Databento
# 2.4M-row 1m MNQ tape. Produces:
#   reports/backtest_real.md            — variant comparison table
#   reports/backtest_real_trades.csv    — full trade log
#   data/backtest_real_daily.json       — per-day PnL for gate revalidation
#   reports/backtest_real_analysis.md   — deep PnL decomposition
_PHASE_12_STAGES: list[Stage] = [
    Stage("backtest_real", "Phase 12", [PYTHON, "scripts/backtest_real.py"]),
    Stage("backtest_real_analysis", "Phase 12", [PYTHON, "scripts/backtest_real_analysis.py"]),
]


# H4 closure (Red Team review 2026-04-25): 9-gate promotion enforcement.
# The Red Team observed that run_all_phases.py was non-blocking on every
# gate -- "70/9-fail" runs returned rc=1 but the operator's
# is_paper_promoted check could read past it. These 9 stages encode the
# concrete pass criteria from docs/next_data_checkpoint.md and
# docs/OPERATOR_BRIEFING_2026_04_25.md.
#
# Each individual gate is non-blocking (so all 9 produce diagnostics
# even when one fails). The final promotion_verdict stage IS blocking:
# it aggregates and returns rc != 0 unless ALL 9 are PASS. That single
# gate is what flips "paper-eligible" to "live-eligible" in the
# operator's promotion workflow.
#
# NO_DATA counts as HOLD -- gates whose underlying artifact does not
# exist yet are treated as failed promotion gates, not skipped ones.
_PHASE_PROMOTION_STAGES: list[Stage] = [
    Stage(
        "promotion_walk_forward_ci_low",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "walk_forward_ci_low"],
    ),
    Stage(
        "promotion_block_bootstrap_ci_low",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "block_bootstrap_ci_low"],
    ),
    Stage(
        "promotion_dsr_search",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "dsr_search"],
    ),
    Stage(
        "promotion_psr_deployment",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "psr_deployment"],
    ),
    Stage(
        "promotion_n_trades_min",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "n_trades_min"],
    ),
    Stage(
        "promotion_regime_stability",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "regime_stability"],
    ),
    Stage(
        "promotion_dow_filter_placebo",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "dow_filter_placebo"],
    ),
    Stage(
        "promotion_knob_wf_sensitivity",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "knob_wf_sensitivity"],
    ),
    Stage(
        "promotion_paper_soak_min_weeks",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--gate", "paper_soak_min_weeks"],
    ),
    # Final aggregator -- blocks the run when ANY of the 9 above did
    # not PASS. This is the single switch that flips "is the strategy
    # promotion-eligible?" from false to true.
    Stage(
        "promotion_verdict",
        "Phase Promotion",
        [PYTHON, "scripts/_promotion_gate.py", "--all"],
        blocking=True,
    ),
]


def _stages(skip: set[str], only: set[str] | None) -> list[Stage]:
    all_stages: list[Stage] = [
        Stage(
            "firm_bridge",
            "Phase 0",
            [PYTHON, "scripts/firm_bridge.py", "--integrate"],
            blocking=False,
        ),
        Stage(
            "live_sim",
            "Phase 0",
            [PYTHON, "scripts/live_sim.py"],
            blocking=True,
        ),
        Stage(
            "replay_journal",
            "Phase 2",
            [PYTHON, "scripts/replay_journal.py"],
            blocking=True,
        ),
        Stage(
            "crash_recovery",
            "Phase 1",
            [PYTHON, "scripts/crash_recovery_test.py", "--n-events", "200"],
        ),
        Stage(
            "strategy_registry",
            "Cross",
            [PYTHON, "scripts/strategy_registry.py", "--update"],
        ),
        Stage(
            "strategy_ab",
            "Phase 3",
            [PYTHON, "scripts/strategy_ab.py", "--winner-only"],
        ),
        Stage(
            "walk_forward",
            "Cross",
            [
                PYTHON,
                "scripts/walk_forward.py",
                "--train",
                "8",
                "--test",
                "3",
                "--stride",
                "1",
                "--variants",
                "r5_real_wide_target",
                "t16_r5_long_only",
                "t17_r5_short_only",
            ],
        ),
        Stage(
            "firm_vs_baseline",
            "Phase 3",
            [PYTHON, "scripts/firm_vs_baseline.py"],
        ),
        Stage(
            "firm_vs_baseline_apex_real",
            "Phase 3",
            [
                PYTHON,
                "scripts/firm_vs_baseline.py",
                "--with-apex-gate",
                "--apex-source",
                "real",
                "--output",
                "reports/firm_vs_baseline_apex_real.md",
            ],
        ),
        Stage(
            "shadow_trader",
            "Phase 8",
            [PYTHON, "scripts/shadow_trader.py"],
        ),
        Stage(
            "shadow_parity",
            "Phase 8",
            [PYTHON, "scripts/shadow_parity.py"],
        ),
        # RETIRED 2026-04-24: shadow_trader.py CLI was simplified. The
        # --gauntlet, --v16, --output flags no longer exist. gauntlet_stats
        # imports DaySummary/GauntletTradeResult/run_shadow which were
        # removed from shadow_trader in the same refactor. The existing
        # reports (shadow_venue_gated.md, shadow_venue_v16.md,
        # gauntlet_stats.md) are preserved as historical artifacts. If this
        # pipeline is needed again, rebuild against the current ShadowTrader
        # class API (scripts/shadow_trader.py:146).
        # Stage("gauntlet_shadow", ...) — retired
        # Stage("gauntlet_stats", ...) — retired
        # Stage("shadow_v16", ...) — retired
        Stage(
            "gauntlet_weight_sweep",
            "Phase 8",
            [PYTHON, "scripts/gauntlet_weight_sweep.py"],
        ),
        Stage(
            "shadow_sensitivity",
            "Phase 8",
            [PYTHON, "scripts/shadow_sensitivity.py"],
        ),
        Stage(
            "gauntlet_weight_sweep_full",
            "Phase 8",
            [PYTHON, "scripts/gauntlet_weight_sweep_full.py", "--max-days", "200"],
        ),
        Stage(
            "hard_gate_sweep",
            "Phase 9",
            [PYTHON, "scripts/hard_gate_sweep.py", "--max-days", "200"],
        ),
        Stage(
            "hard_gate_attribution",
            "Phase 9",
            [PYTHON, "scripts/hard_gate_attribution.py"],
        ),
        Stage(
            "gate_pnl_attribution",
            "Phase 10",
            [PYTHON, "scripts/gate_pnl_attribution.py"],
        ),
        Stage(
            "ow_validation",
            "Phase 11",
            [PYTHON, "scripts/ow_validation.py"],
        ),
        Stage(
            "rolling_calibration",
            "Phase 7",
            [PYTHON, "scripts/rolling_calibration.py"],
        ),
        Stage(
            "calibration",
            "Phase 3",
            [PYTHON, "scripts/calibration.py"],
        ),
        Stage(
            "bayesian_expectancy",
            "Phase 5",
            [PYTHON, "scripts/bayesian_expectancy.py"],
        ),
        Stage(
            "firm_review_markdown",
            "Phase 3",
            [PYTHON, "scripts/firm_review.py", "--variant", "r5_real_wide_target"],
        ),
        Stage(
            "firm_live_review",
            "Phase 3",
            [PYTHON, "scripts/firm_live_review.py", "--variant", "r5_real_wide_target"],
        ),
        Stage(
            "postmortem",
            "Phase 3",
            [PYTHON, "scripts/postmortem.py", "--mode", "all"],
        ),
        Stage(
            "daily_digest",
            "Phase 1",
            [PYTHON, "scripts/daily_digest.py"],
        ),
    ]

    # Append Phase A-D after the core pipeline.
    all_stages.extend(_PHASE_A_STAGES)
    all_stages.extend(_PHASE_B_STAGES)
    all_stages.extend(_PHASE_C_STAGES)
    all_stages.extend(_PHASE_D_STAGES)
    all_stages.extend(_PHASE_E_STAGES)
    all_stages.extend(_PHASE_F_STAGES)
    all_stages.extend(_PHASE_12_STAGES)
    # H4 closure: the 9 promotion gates run last so they evaluate
    # everything the prior phases produced. The final verdict stage is
    # blocking -- the orchestrator returns rc != 0 unless all 9 PASS.
    all_stages.extend(_PHASE_PROMOTION_STAGES)

    if only:
        all_stages = [s for s in all_stages if s.name in only]
    if skip:
        all_stages = [s for s in all_stages if s.name not in skip]
    return all_stages


def _run(stage: Stage) -> StageResult:
    t0 = time.monotonic()
    # Force UTF-8 for subprocess stdout/stderr. Fixes 37/79 stages that
    # emit Unicode (→, −, ✓) and crash on Windows' default cp1252 codec.
    # Added 2026-04-24 by firm-daily-orchestrator scheduled task.
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(
        stage.cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    dur = time.monotonic() - t0
    stdout_tail = "\n".join(proc.stdout.splitlines()[-10:])
    stderr_tail = "\n".join(proc.stderr.splitlines()[-10:])
    return StageResult(
        name=stage.name,
        phase=stage.phase,
        returncode=proc.returncode,
        duration_s=dur,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )


def _render(results: list[StageResult]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    ok = sum(1 for r in results if r.ok)
    total = len(results)
    lines = [f"# Run-All-Phases — {now}", ""]
    lines.append(f"- Stages: **{total}**")
    lines.append(f"- Passed: **{ok}/{total}**")
    lines.append("")

    # Per-phase ledger
    from collections import defaultdict

    by_phase: dict = defaultdict(list)
    for r in results:
        by_phase[r.phase].append(r)
    lines.append("## Per-phase summary")
    lines.append("")
    lines.append("| Phase | Passed | Total |")
    lines.append("|---|---:|---:|")
    for phase in sorted(by_phase):
        rs = by_phase[phase]
        lines.append(f"| {phase} | {sum(1 for r in rs if r.ok)} | {len(rs)} |")
    lines.append("")

    lines.append("## Stage ledger")
    lines.append("")
    lines.append("| # | Phase | Stage | Status | Duration (s) |")
    lines.append("|---:|---|---|---|---:|")
    for i, r in enumerate(results, 1):
        status = "OK" if r.ok else f"FAIL (rc={r.returncode})"
        lines.append(f"| {i} | {r.phase} | `{r.name}` | {status} | {r.duration_s:.1f} |")
    lines.append("")

    failures = [r for r in results if not r.ok]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for r in failures:
            lines.append(f"### `{r.name}` ({r.phase})")
            lines.append("")
            lines.append("```")
            lines.append("# stdout (last 10 lines)")
            lines.append(r.stdout_tail or "<empty>")
            lines.append("# stderr (last 10 lines)")
            lines.append(r.stderr_tail or "<empty>")
            lines.append("```")
            lines.append("")
    else:
        lines.append("## Failures")
        lines.append("")
        lines.append("_None — all phases green._")
        lines.append("")

    lines.append("## Output index")
    lines.append("")
    lines.append("- `reports/live_sim_analysis.md` — Phase 0 internal-sim summary")
    lines.append("- `reports/replay_audit.md` — Phase 2 determinism")
    lines.append("- `reports/crash_recovery.md` — Phase 1 durability")
    lines.append("- `reports/strategy_v2_report.md` — Phase 3 A/B winner")
    lines.append("- `reports/walk_forward.md` — Cross-cutting out-of-sample edge")
    lines.append(
        "- `reports/firm_vs_baseline.md` — Phase 3 Firm filter justification (synthetic Apex)"
    )
    lines.append(
        "- `reports/firm_vs_baseline_apex_real.md` — Phase 3 Firm filter (real Apex — Batch 3F/3H)"
    )
    lines.append("- `reports/shadow_venue.md` — Phase 8 Shadow-venue dry-run (Batch 4A — scaffold)")
    lines.append("- `reports/shadow_parity.md` — Phase 8 Shadow→Sim parity check (Batch 4C)")
    lines.append(
        "- `reports/shadow_venue_gated.md` — Phase 8 Shadow-venue with gauntlet pre-filter (Batch 5A)"
    )
    lines.append("- `reports/gauntlet_stats.md` — Phase 8 Gauntlet A/B comparison (Batch 5A)")
    lines.append(
        "- `reports/shadow_venue_v16.md` — Phase 8 Shadow-venue with V16 gauntlet blend (Batch 5D)"
    )
    lines.append(
        "- `reports/gauntlet_weight_sweep.md` — Phase 8 Walk-forward weight optimization (Batch 5D)"
    )
    lines.append(
        "- `reports/shadow_sensitivity.md` — Phase 8 Slippage/latency/partial-fill sensitivity (Batch 6C)"
    )
    lines.append(
        "- `reports/rolling_calibration.md` — Phase 7 Per-epoch rolling calibration drift (Batch 7C)"
    )
    lines.append(
        "- `reports/gauntlet_weight_sweep_full.md` — Phase 8 Full-sample V16 weight sweep (Batch 8A)"
    )
    lines.append("- `reports/hard_gate_sweep.md` — Phase 9 Hard-gate threshold sweep (Batch 9B)")
    lines.append(
        "- `reports/hard_gate_attribution.md` — Phase 9 Hard-gate day attribution (Batch 9B)"
    )
    lines.append(
        "- `reports/gate_pnl_attribution.md` — Phase 10 Per-gate PnL attribution + outcome weights (Batch 10A/10B)"
    )
    lines.append(
        "- `reports/ow_validation.md` — Phase 11 OOS validation of outcome weights (Batch 11A)"
    )
    lines.append("- `reports/calibration.md` — Phase 3 ml_scorer calibration")
    lines.append("- `reports/bayesian_expectancy.md` — Phase 5 posteriors + heat")
    lines.append("- `reports/firm_reviews/<variant>.md` — Phase 3 markdown Firm memo")
    lines.append("- `reports/firm_reviews/<variant>_live.md` — Phase 3 LIVE Firm verdict")
    lines.append("- `reports/post_mortems/*.md` — Phase 3 per-trade post-mortems")
    lines.append("- `reports/daily/YYYY-MM-DD.md` — Phase 1 end-of-session digest")
    lines.append("- `reports/firm_integration.md` — Phase 0 Firm bridge probe")
    lines.append(
        "- `reports/alerting.md` · `reports/edge_decay.md` · `reports/mae_mfe.md` · `reports/time_heatmap.md` — Phase A"
    )
    lines.append(
        "- `reports/counterfactual.md` · `reports/trade_governor.md` · `reports/rule_adherence.md` · `reports/email_recap.md` · `reports/time_exit.md` — Phase A"
    )
    lines.append(
        "- `reports/psych_sidecar.md` · `reports/pre_trade_pause.md` · `reports/loss_streak.md` · `reports/hot_hand.md` — Phase B"
    )
    lines.append(
        "- `reports/auto_screenshot.md` · `reports/voice_memos.md` · `reports/weekly_review.md` · `reports/monthly_narrative.md` · `reports/mistake_taxonomy.md` · `reports/ai_reviewer.md` — Phase B"
    )
    lines.append(
        "- `reports/cumulative_delta.md` · `reports/volume_profile.md` · `reports/sector_rotation.md` · `reports/news_feed.md` · `reports/gex_monitor.md` — Phase C"
    )
    lines.append(
        "- `reports/vix_term.md` · `reports/breadth.md` · `reports/event_calendar.md` · `reports/earnings_amp.md` · `reports/seasonality.md` — Phase C"
    )
    lines.append(
        "- `reports/gbm_filter.md` · `reports/shap_rank.md` · `reports/trade_clusters.md` · `reports/anomaly.md` · `reports/heartbeat.md` — Phase D"
    )
    lines.append(
        "- `reports/deadman_switch.md` · `reports/encrypted_backup.md` · `reports/tax_1256.md` · `reports/pretrade_checklist.md` · `reports/correlation_cap.md` — Phase D"
    )
    lines.append(
        "- `reports/gate_chain.md` · `reports/parity.md` · `reports/gauntlet.md` — Phase E (enforcement spine)"
    )
    lines.append(
        "- `reports/burn_in.md` · `reports/eta_v3.md` · `reports/eta_v3_probe.md` · `reports/eta_v3_enrich.md` · `reports/eta_v3_meta.md` — Phase F (closing reporters; `eta_v3_bridge.md` retained as legacy alias)"
    )
    lines.append(
        "- `reports/backtest_real.md` · `reports/backtest_real_analysis.md` · `reports/backtest_real_trades.csv` — Phase 12 (real-tape backtest; `data/backtest_real_daily.json` for gate revalidation)"
    )
    return "\n".join(lines) + "\n"


def _pre_run_shim_guard() -> None:
    """Self-heal OneDrive-truncated files before the 14-stage sweep.

    The shim guard is a hard prerequisite — without it, firm_bridge /
    firm_live_review / any phase that imports firm_runtime will crash on
    a truncated file. Called before ``_stages`` so the first stage sees
    a clean repo state.
    """
    # Same src-layout trickery the conftest uses so the guard imports
    # even when the script is run from the repo root.
    from pathlib import Path

    _repo = Path(__file__).resolve().parents[1]
    _src = _repo / "src"
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))
    try:
        from mnq._shim_guard import heal_all_guarded_files
    except Exception as e:
        print(f"! shim guard unavailable ({e!r}) — continuing without heal", flush=True)
        return
    healed = heal_all_guarded_files(raise_on_failure=False)
    broken = {n: h.reason for n, h in healed.items() if not h.ok}
    if broken:
        print(f"! shim guard could not heal: {broken}", flush=True)
    else:
        print(f"✓ shim guard: {len(healed)} files verified clean", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all roadmap phases end-to-end.")
    parser.add_argument("--skip", nargs="*", default=[], help="Stages to skip by name.")
    parser.add_argument("--only", nargs="*", default=None, help="Run only these stages.")
    parser.add_argument(
        "--no-shim-guard", action="store_true", help="Skip the pre-run OneDrive self-heal sweep."
    )
    args = parser.parse_args(argv)

    if not args.no_shim_guard:
        _pre_run_shim_guard()

    stages = _stages(skip=set(args.skip), only=set(args.only) if args.only else None)
    if not stages:
        print("no stages selected", file=sys.stderr)
        return 2

    print(f"running {len(stages)} stages...")
    results: list[StageResult] = []
    blocking_fail = False
    for s in stages:
        if blocking_fail:
            print(f"× [{s.phase}] {s.name} — skipped due to earlier blocking fail", flush=True)
            continue
        print(f"→ [{s.phase}] {s.name} ...", flush=True)
        r = _run(s)
        results.append(r)
        status = "OK" if r.ok else f"FAIL rc={r.returncode}"
        print(f"   {status} ({r.duration_s:.1f}s)", flush=True)
        if not r.ok and s.blocking:
            blocking_fail = True

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = _render(results)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\nreport: {REPORT_PATH}", flush=True)

    n_ok = sum(1 for r in results if r.ok)
    n_fail = sum(1 for r in results if not r.ok)
    print(f"summary: {n_ok} ok / {n_fail} fail / {len(stages)} total", flush=True)

    return 0 if blocking_fail is False and n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
