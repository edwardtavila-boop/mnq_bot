# Data Sources for the Firm Command Center

This document maps every data point in the dashboard to its source report file. When regenerating the artifact, read these files and extract the values described below.

## Report File → Data Mapping

### `reports/run_all_phases.md`
- Parse the markdown table under "## Stage ledger"
- Extract: stage number, phase, stage name, status (OK/FAIL), duration in seconds
- Also extract: total stages count, passed count, run timestamp
- Used in: **Orchestrator tab**, **Command Center** KPIs

### `reports/live_sim_analysis.md`
- **Pipeline counters section:** signals emitted, orders submitted, entry fills, round trips closed, blocked by risk, breaker halts, malformed events
- **Paper-sim summary:** closed trades, net PnL, expectancy/trade, win rate, avg slippage
- **Slippage statistics:** mean, median, stdev, p95 adverse, p05 favourable
- **Slippage by regime:** normal vs high vol mean slippage
- **Per-regime PnL table:** regime, trades, wins, win%, net PnL, avg slip
- **Turnover drift:** expected mu/sigma, realized, z-score, anomalous?
- **Reconciliation:** diffs count, critical diffs
- Used in: **Command Center** (KPIs, regime table), **Risk tab** (slippage, turnover)

### `reports/firm_reviews/<variant>_live.md`
- **Strategy spec JSON block:** sample_size, expected_expectancy_r, oos_degradation_pct, entry_logic, stop_logic, target_logic, dd_kill_switch_r, regimes_approved
- **Stage verdicts table:** stage name, verdict, P(ok), 95% CI, horizon
- **Per-stage detail sections:** reasoning, primary driver, secondary driver, falsification criteria, payload JSON (contains violations, attacks, Kelly, sizing)
- Used in: **Firm Verdict tab**, **Command Center** agent pipeline

### `reports/bayesian_expectancy.md`
- Parse the table with columns: regime, side, variant, n, post_wr, ci_lo, ci_hi, post_exp, ci_lo_exp, heat_cap
- Heat cap: 1 = OPEN (tradeable), 0 = CAPPED (CI includes zero or negative)
- Used in: **Risk tab** (Bayesian posterior table)

### `reports/walk_forward.md`
- **Config line:** train window, test window, stride, folds count
- **Aggregate section:** total test PnL, total test trades, mean PnL/fold, stdev PnL/fold, positive folds count
- **Per-fold table:** fold id, selected variant, train PnL, test PnL, test trades, test win rate
- **Winner stability:** how many folds the winner won
- Used in: **Walk-Forward tab**

### `reports/calibration.md`
- **Summary metrics:** n, base rate, Brier (in-sample), log-loss (in-sample), Brier (LOOCV), log-loss (LOOCV)
- **Reliability curve table:** predicted mean, realized win rate, bucket n
- Used in: **Risk tab** (calibration section)

### `reports/firm_vs_baseline.md`
- **Headline comparison table:** filtered vs baseline trades, net PnL, win rate, expectancy/trade
- **Daily lift:** total lift, 95% bootstrap CI low/high
- **Verdict line:** FIRM FILTER JUSTIFIED or NOT JUSTIFIED
- Used in: **Command Center** (firm filter section), **Walk-Forward tab** (lift panel)

### `reports/firm_integration.json`
- JSON with: firm_path, path_exists, modules (importable, resolved counts), missing list, ready boolean, error
- Used in: **Command Center** (system health section, bridge status)

### `reports/strategy_registry.md` / `reports/strategy_registry.json`
- Total variant count (active + graveyard)
- Active variant names
- Used in: **Command Center** (registry KPI)

### `reports/daily/<YYYY-MM-DD>.md`
- Today's date, first/last event times, total events
- Closed trades, gross PnL, win rate, mean slippage
- Biggest winner/loser with trade ID and regime
- Per-regime and per-side PnL breakdown
- Event type counts
- Used in: **Command Center** (today's stats)

### `reports/crash_recovery.md`
- Events recovered count, total events, errors, OK status
- Used in: **Roadmap** (Phase 1 task verification)

### `reports/gate_chain.md` — Phase E enforcement spine
- Parse the per-gate traffic-light table (order: TimeOfDay, Session, NewsBlackout,
  VolatilityRegime, CorrelationCap, HeatBudget, GauntletGates, KillSwitch)
- Extract: gate name, status (PASS/HOLD/BLOCK), reason string
- Also: trace id count for the evaluation window
- Used in: **Enforcement Spine tab** (gate chain column)

### `reports/parity.md` — Phase E enforcement spine
- Parse the parity summary: live_sim PnL, baseline PnL, diff in $ and in R
- Tolerance line: pass/fail + tolerance threshold used
- Per-fill divergence count (if any)
- Used in: **Enforcement Spine tab** (parity column), **Walk-Forward tab** (cross-check)

### `reports/gauntlet.md` — Phase E enforcement spine (12 gates)
- Gate rollup: passed / total, per-gate PASS/FAIL
- Extract: gate name, verdict, numeric value where applicable
  (sample_size, oos_degradation, dd_kill_r, turnover_z, expectancy_r, heat cap hit, etc.)
- Overall verdict: SHIPPABLE / NOT SHIPPABLE
- Used in: **Enforcement Spine tab** (gauntlet column), **Firm Verdict tab** (gauntlet badge)

### `reports/backtest_baseline.md` + `reports/backtest_baseline.json`
- Idealized zero-slippage replay comparison target for parity_harness
- Used in: **Walk-Forward tab** (baseline reference line)

### `reports/burn_in.md` — Phase F durability
- Compressed 72h burn-in: monotonic sequence validated, deterministic checksum,
  RSS drift (MB), avg events/s, total events
- Used in: **Enforcement Spine tab** (durability column), **Roadmap** (Phase 1 burn-in check)

### `reports/eta_v3.md` — Phase F Apex V3 bridge (primary; legacy alias `reports/eta_v3_bridge.md`)
- Engine availability flag (🟢/🔴) and reason line if unavailable
- Voices exposed count, `evaluate` / `detect_regime` callability
- Single-line voice summary (regime, pm_final, quant_total, red_team, agreement, setup)
- Payload enrichment diff — base vs keys added (`eta_v3_voices`, `eta_v3_pm_final`,
  `eta_v3_regime`, `eta_v3_direction`)
- Fail-open stub proof: `build_enrichment_payload(base, None) == base`
- Used in: **Apex V3 tab** (all panels)

### `reports/eta_v3_probe.md` — Phase F Apex V3 fast probe
- Engine availability flag + reason string
- Voices found count, `evaluate` / `detect_regime` callability booleans
- Raw voice name list for inspection
- Used in: **Apex V3 tab** (engine availability widget)

### `reports/eta_v3_enrich.md` — Phase F Apex V3 AgentInput enrichment
- Snapshot source (live engine vs synthetic fallback)
- AgentInput source (real shim-built vs duck-typed stub)
- Single-line voice summary
- Payload key diff: before vs after, explicit added-keys list
- JSON dump of added payload content
- Used in: **Apex V3 tab** (enrichment diff panel), **Firm Verdict tab**
  (shows that Quant-stage input is actually enriched)

### `<eta_engine>/docs/premarket_latest.json` — Jarvis premarket briefing
- Produced by `eta_engine.scripts.daily_premarket` (07:00 ET cron).
- Schema: `ts`, `macro{vix_level,macro_bias,next_event_label,hours_until_next_event}`,
  `equity{account_equity,daily_pnl,daily_drawdown_pct,open_positions,open_risk_r}`,
  `regime{regime,confidence,previous_regime,flipped_recently}`,
  `journal{kill_switch_active,autopilot_mode,executed_last_24h,blocked_last_24h,overrides_last_24h,correlations_alert}`,
  `suggestion{action,reason,confidence,warnings}`, `notes[]`.
- `action` is one of: TRADE, STAND_ASIDE, REDUCE, REVIEW, KILL.
- Used in: **Jarvis tab** (banner + all panels), **Command Center** tab (Jarvis KPI +
  top-bar pill), top bar (JARVIS <ACTION>).

### `<eta_engine>/docs/weekly_checklist_latest.json` — 10-principle report card
- Produced by `eta_engine.scripts.weekly_review --checklist-answers ...`.
- Schema: `ts`, `period_label`, `score` (0..1), `letter_grade`, `discipline_score` (0..10),
  `answers[{index,yes,note}]`, `critical_gaps[slug]`.
- Index map (fixed): 0 a_plus_only, 1 process_over_outcome, 2 decision_log,
  3 consult_jarvis, 4 never_autopilot, 5 cadence_of_review, 6 stress_testing,
  7 risk_discipline, 8 override_discipline, 9 continuous_learning.
- Used in: **Jarvis tab** (10 Principles report card), **Command Center** tab
  (Discipline KPI).

### `<eta_engine>/docs/monthly_review_latest.json` — Monthly deep review
- Produced by `eta_engine.scripts.monthly_deep_review` (1st of month).
- Schema: `period`, `generated_at_utc`, `grading{n, distribution, mean_total}`,
  `exit_quality{n, heatmap?}`, `rationales{n}`, `proposed_tweaks[]`,
  `inputs{trades_path, mae_mfe_path, rationales_path}`.
- Used in: **Jarvis tab** (monthly deep review panel).

## Verification rules

When populating the artifact, verify:
1. Every number has a source file — no invented data
2. If a report is missing, show "N/A" or "—" instead of guessing
3. Stage pass/fail must match `run_all_phases.md` exactly
4. Firm verdict must match the `_live.md` file for the active variant
5. Roadmap task completion is derived from: does the report exist AND did the corresponding stage pass?
