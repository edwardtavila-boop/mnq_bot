---
name: firm-tracker
description: Generate the EVOLUTIONARY TRADING ALGO Command Center dashboard — a React artifact showing the full equity sniper roadmap, orchestrator status, Firm verdict, risk metrics, walk-forward results, calibration data, enforcement spine, durability, confluence engine, and Apex V3 bridge. Use this skill whenever the user asks to "show the dashboard", "show the tracker", "run the phases", "show firm status", "command center", "phase tracker", "show results", "what's the status", or any request to visualize the current state of the EVOLUTIONARY TRADING ALGO + Firm system. Also trigger when the user says "update the tracker", "refresh the dashboard", or "regenerate the command center". This skill reads live data from the reports directory and generates a polished React artifact with 8 tabs.
---

# EVOLUTIONARY TRADING ALGO // Command Center — Dashboard Generator

This skill generates a React (.jsx) artifact that serves as the elite command center for the EVOLUTIONARY TRADING ALGO + Firm trading system. It reads live data from report files and renders an 8-tab interactive dashboard.

## When to use

- User asks to see the dashboard, tracker, status, or results
- User asks to run phases and show results
- User asks to update/refresh the command center
- After running `run_all_phases.py` and wanting to visualize output
- Any request to understand the current state of the project

## Workflow

### Step 1: Run the orchestrator (if requested or stale)

If the user wants fresh data, run the orchestrator first:

```bash
cd /path/to/mnq_bot && .venv/bin/python scripts/run_all_phases.py
```

If the user just wants to view existing data, skip this step.

### Step 2: Extract live data from reports

Read the `references/data_sources.md` file for the complete list of report files and what data to extract from each. The key reports are:

- `reports/run_all_phases.md` — stage ledger (pass/fail, durations)
- `reports/live_sim_analysis.md` — sim stats (PnL, win rate, slippage, regime breakdown)
- `reports/firm_reviews/<variant>_live.md` — Firm verdict (6 stage verdicts with probabilities, attacks)
- `reports/bayesian_expectancy.md` — posterior win rates, heat budgets per regime
- `reports/walk_forward.md` — fold results, OOS edge
- `reports/calibration.md` — Brier, log-loss, reliability curve
- `reports/firm_vs_baseline.md` — filtered vs baseline PnL lift
- `reports/firm_integration.json` — bridge status (ready, contract)
- `reports/strategy_registry.md` — active variant count
- `reports/daily/<date>.md` — today's session stats
- `reports/crash_recovery.md` — durability test
- `reports/gate_chain.md` · `reports/gauntlet.md` · `reports/parity.md` — Phase E enforcement spine
- `reports/pnl_report.md` — idealized backtest baseline (zero-slippage replay of live_sim fills)
- `reports/burn_in.md` — Phase F 72h compressed burn-in (monotonic seq, deterministic checksum, RSS drift)
- `reports/eta_v3.md` — Phase F Apex V3 → Firm adapter bridge (15-voice engine availability + payload enrichment diff). Legacy alias: `reports/eta_v3_bridge.md`.
- `reports/eta_v3_probe.md` — Phase F Apex V3 fast probe (engine importable, voice count, evaluate/detect_regime callability)
- `reports/eta_v3_enrich.md` — Phase F Apex V3 AgentInput enrichment verifier (before/after payload key diff against a real-or-stubbed AgentInput)

### Step 3: Generate the artifact

Use the template in `references/artifact_template.md` as the base. Populate the data constants at the top of the file with the extracted values. The artifact has 8 tabs:

1. **Command Center** — KPIs, agent pipeline overview, regime PnL, firm filter lift, system health
2. **Roadmap** — 10-phase roadmap with expandable task checklists and progress bars
3. **Orchestrator** — 60+-stage ledger with pass/fail, durations, share bars (includes Phase A-F)
4. **Firm Verdict** — spec payload, 6-stage adversarial review with clickable detail panels showing reasoning, falsification, and attacks
5. **Risk & Calibration** — Bayesian posteriors, heat caps, slippage stats, ML scorer calibration buckets
6. **Walk-Forward** — fold-by-fold OOS results, firm filter lift, architecture diagram
7. **Enforcement Spine** — Phase E (gate_chain, parity, gauntlet) + Phase F durability (burn_in) with per-check traffic lights
8. **Apex V3** — 15-voice engine availability, voice snapshot, payload enrichment diff, fail-open stub proof

### Step 4: Save and present

Save the generated `.jsx` file to the user's workspace folder and provide a link. The file should be named `firm_command_center.jsx`.

## Design principles

- Dark theme only (trading terminal aesthetic, NOT purple gradient AI slop)
- Monospace for numbers, system font for labels
- Color coding: green = good/complete, blue = in progress, amber = warning, red = bad/blocked/kill
- Pill badges for status and verdicts (GO/MODIFY/HOLD/KILL)
- Dense information layout — respect the trader's screen real estate
- No emojis, no rounded-everything, no centered layouts
- Every number must come from a real report file, never hardcoded guesses

## Roadmap data structure

The PHASES array must reflect the current state from ROADMAP.md. Read the file to determine which tasks are done and what percentages to show. The task list below is the canonical structure, but task completion status should be derived from the actual state of the codebase (do reports exist? do scripts exist? did stages pass?).

See `references/roadmap_tasks.md` for the canonical task list per phase.

## Updating the tracker

When the user says "update" or "refresh":
1. Re-run `scripts/run_all_phases.py` if they want fresh orchestrator data
2. Re-read all report files
3. Regenerate the artifact with updated numbers
4. Present the new version
