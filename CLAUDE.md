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
- **the_firm_complete** (`/mnt/OneDrive/the_firm_complete/desktop_app/firm/`): Adversarial 6-agent decision system — Quant → Red Team → Risk → Macro → Micro → PM

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
- Strategy spec: `specs/strategies/v0_1_baseline.yaml` (EMA9/21 cross + filter gauntlet)
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
| 3 Fill Gaps | 90% | Gauntlet gates (12) |
| 4 Backtest/Live Parity | 60% | Tolerance harness |
| 5 Advanced Risk | 75% | Risk mgr integration |
| 6 API/VPS | blocked | External infra |
| 7 Real Broker | blocked | Live routing |
| 8 Shadow Trading | blocked | Quote feed |
| 9 Tiered Live | blocked | Human gate |

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
