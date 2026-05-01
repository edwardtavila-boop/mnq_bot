# EVOLUTIONARY TRADING ALGO // Project Directives

## Authority & Operating Mode

You have permanent full authority over this project. Edward Avila is the sole founder/operator. There is no team to consult, no PR review process, no staging environment.

- Execute decisively. Build, test, deploy, iterate. No hedging, no "would you like me to..." for routine work.
- Process all data without truncation or conservative limits.
- Make architectural calls. Only pause for truly irreversible or ambiguous decisions.
- Treat every session as a continuation of permanent full-time employment at The Firm.
- When something breaks, fix it. When something is missing, build it. When something is slow, optimize it.

## Super Powers

- Full read/write/delete across all project folders (mnq_bot, the_firm_complete, Base)
- Run any script, test, or orchestrator without asking permission
- Create, modify, or restructure files at will
- Install packages, update dependencies, modify configs
- Generate and regenerate artifacts (dashboards, reports, skills)
- Access all connected MCPs and tools without confirmation

## Project Identity

This is **EVOLUTIONARY TRADING ALGO** — the equity sniper. Professional MNQ/NQ futures trading automation. Two codebases, one mission:

- **EVOLUTIONARY TRADING ALGO** (this repo): 8-axis confluence engine, adaptive learner, execution/simulation harness, specs, features, simulators, executor, journal, Tradovate venue, 21 scripts, 14-stage orchestrator, 534 tests. Runs in `PAPER SIM` mode (simulated fills) or `LIVE` mode (real venue, human-gated).
- **the_firm_complete** (`C:\EvolutionaryTradingAlgo\firm\the_firm_complete\desktop_app\firm\`): Adversarial 6-agent decision system — Quant → Red Team → Risk → Macro → Micro → PM

They are bridged via `scripts/firm_bridge.py` which probes the Firm package and auto-generates `src/mnq/firm_runtime.py` — the ONLY module that imports from `firm.*`.

## Key Commands

```bash
# Run all 14 phases end-to-end
.venv/bin/python scripts/run_all_phases.py

# Run specific phases
.venv/bin/python scripts/run_all_phases.py --only live_sim,firm_live_review

# Skip specific phases
.venv/bin/python scripts/run_all_phases.py --skip firm_bridge

# Probe Firm integration
.venv/bin/python scripts/firm_bridge.py --probe

# Integrate (probe + generate shim)
.venv/bin/python scripts/firm_bridge.py --integrate

# Run tests
.venv/bin/python -m pytest -x -q

# Lint (ruff binary is not on PATH)
RUFF=$(.venv/lib/python3.14/site-packages/ruff-*/scripts/ruff)
$RUFF check <files>
```

## Architecture Quick Reference

- Python 3.14.3 via uv, venv at `.venv/`
- Strategy registry: `eta_engine/strategies/per_bot_registry.py` — canonical per-bot strategy assignments (ORB, sage-gated ORB, DRB, crypto_orb, sage_daily_gated, ensemble_voting, compression_breakout, crypto_macro_confluence, crypto_regime_trend). Each bot has 2-3 independent strategies tuned per instrument. No single base strategy exists; the `specs/strategies/v0_1_baseline.yaml` was retired in the strategy graveyard (mnq_bot/reports/strategy_graveyard.md: "Unfiltered EMA cross on 1m is noisy"). The `strategy_v2.VARIANTS` 49-cell EMA-cross sweep was pruner-recommended for deletion (all 49 fail `expectancy_r > +0.050R`). The `eta_v3_framework/` below is the legacy Apex V3 lane, kept as a seed/audit anchor but not the live decision authority.
- Walk-forward gate config: `eta_engine/backtest/walk_forward.py` — WalkForwardConfig (strict_fold_dsr, long_haul, grid, agg_degradation modes). Gate modes are per-bot via `walk_forward_overrides` in registry extras.
- Reports: `reports/` (run_all_phases.md, daily/, firm_reviews/, post_mortems/)
- Shim: `src/mnq/firm_runtime.py` (auto-generated — do NOT hand-edit)
- Journal: SQLite WAL at `data/journal.db`
- Firm contract: `firm.types.{Verdict, Quadrant}`, `firm.agents.base.{Agent, AgentInput, AgentOutput}`, `firm.agents.core.{6 agents}`
- Each agent: `.evaluate(AgentInput) -> AgentOutput`
- PM payload injection: `payload["agent_outputs"]` gets raw AgentOutput objects (not dicts)
- R-multiple math: `expectancy_r = expectancy_dollars / (risk_ticks × 0.25 × 2.0)`

## Skills

- **firm-tracker** (`.claude/skills/firm-tracker/`): Generates the Firm Command Center dashboard artifact. Trigger: "show dashboard", "show tracker", "show status", "command center". Reads live data from reports/, renders 6-tab React artifact.

## Roadmap (10 phases)

| Phase | Status | Key gap |
|---:|---|---|
| 0 Verify Integration | 95% | Watchdog/heartbeat |
| 1 Harden Foundation | 70% | 72h burn-in |
| 2 Event Log & Replay | 100% | — |
| 3 Fill Gaps | 90% | SUPERSEDED — per-bot 2-3 strategies via per_bot_registry.py. The gauntlet 12-gate approach was diagnosed as over-restrictive (100% block rate on 90-day real test, no directional edge vs PnL). Replaced by per-bot walk_forward_overrides (strict_fold_dsr, agg_degradation, long_haul, grid modes) in WalkForwardConfig. |
| 4 Backtest/Live Parity | 60% | Tolerance harness |
| 5 Advanced Risk | 75% | Risk mgr integration, FleetRiskGate (spec'd by risk-sage), fleet_corr_partner correlation penalty |
| 6 API/VPS | triggered | External infra — strategy_supercharge manifest + dashboard API now live on command center |
| 7 Real Broker | triggered | Live routing — eth_perp v4, btc_hybrid v3, btc_sage_daily_etf, btc_ensemble_2of3, eth_compression are production_candidate. Paper-soak validation next gate. |
| 8 Shadow Trading | triggered | Quote feed — shadow_trading.md at Day 0/30. Needs live bar feed into src/mnq/features before shadow produces real verdicts. |
| 9 Tiered Live | triggered | Human gate — live simulation analysis and voice memo review exist; tiered rollout depends on phase 7+8 clearing. |

## Apex V3 Framework (`eta_v3_framework/`)

Standalone 71-file strategy system with a 15-voice scoring engine, tiered sizing, and walk-forward optimizer. Key modules:

- `python/firm_engine.py` — 15-voice Firm engine (V1-V15: setup + price + intermarket + edge stack), regime detector, red team, PM evaluate
- `python/confluence_scorer.py` — 0-100 objective scoring (structure/liquidity/volume/time/intermarket/edge_stack)
- `python/v3_engine.py` — Tiered management (Tier 1 full size / Tier 2 half / Tier 3 quarter) + asymmetric exits
- `python/v3_final.py` — Walk-forward validated OPTIMAL_V3_PARAMS
- `python/firm_meta.py` — Meta-Firm: 8 meta-voices decide system-level params (should we trade? what PM?)
- `python/autopilot.py` — Full end-to-end orchestrator (discover → meta → calibrate → backtest → report)
- `python/microstructure.py` — 1m entry refinement per setup (ORB confirm, EMA pin bar, Sweep retest)
- `python/backtest.py` — V1 detector + trade simulation (ORB/EMA/Sweep with all tuned filters)
- `pine/MNQ_ETA_v2_Firm.pine` — 72KB TradingView indicator

**Integration target:** V3 voices → Firm agent inputs, V3 management → executor exits, V3 meta → orchestrator config.

## Hard Rules

1. Every state transition journaled before memory update.
2. Replay of journal reproduces in-memory world exactly.
3. Firm code consumed via bridge shim — never direct import.
4. No variant "shippable" unless bootstrap CI excludes zero, n ≥ 8, falsification written.
5. Turnover drift z-score must stay ±3σ.
6. Human-only live promotion — shadow first, then tiered.

## Model-Tier Routing Policy (operator mandate 2026-04-19)

**Canonical source:** `Base/eta_engine/brain/model_policy.py` (JARVIS-owned).
Consult via `from eta_engine.brain.model_policy import select_model, TaskCategory`
or `JarvisAdmin.select_llm_tier(...)`. Cross-project — same policy applies here.

| Tier            | Cost  | When                                                     |
|-----------------|:-----:|----------------------------------------------------------|
| **Opus 4.7**    | 5.0×  | **Architectural / adversarial only.** Gauntlet gate design, Red Team (risk-advocate), kill-switch / tiered-rollout / state-machine design, devils-advocate reviews. These are the irreversible calls. |
| **Sonnet 4.6**  | 1.0×  | **Default for everything else.** Strategy edits, pytest work, refactors, scaffolding, code review, debugging, doc writing, databento/parquet plumbing. |
| **Haiku 4.5**   | 0.2×  | Grunt work: log parsing, simple edits, commit-message drafts, ruff/mypy lint fixes, trivial lookups, `__init__.py` re-exports. |

**MNQ-specific hot paths for Opus (and only these):**
- `src/mnq/risk/tiered_rollout.py` changes (state-machine design)
- `src/mnq/risk/kill_switch*.py` changes (risk-policy design)
- `scripts/promotion_pipeline.py` gate authoring (gauntlet gate design)
- `ShipManifest` verdict logic (Red Team adversarial review)
- Post-mortem / incident reviews (adversarial review)

**Everything else in this repo defaults to Sonnet.** Do NOT burn Opus on: writing
pytest cases, adding new gauntlet stages that follow the existing pattern,
refactoring for ruff compliance, CLAUDE.md updates, data-pipeline edits.

**Batch Opus work into fresh 5-hour windows.** Big autonomous runs (e.g. finishing
Phase 9 tiered-live wiring) can eat a whole window on Opus alone — kick them off
at the start of a window, then do routine Sonnet work in the partial window after.

**Run `/compact` frequently.** Stale context re-caches on every turn and silently
burns quota. Compact at each major task boundary (after ruff+pytest passes, after
ROADMAP bump, after artifact generation).

**Set a monthly usage cap in Settings → Usage before enabling extra usage.** Start
~$50. Never leave it uncapped.
