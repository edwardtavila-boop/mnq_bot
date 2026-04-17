# Canonical Roadmap Tasks

This is the master task list for the 10-phase roadmap. When generating the artifact, use this structure and determine completion status by checking whether the corresponding script/report exists and whether the orchestrator stage passed.

## Phase 0 — Verify Integration (target: 95%)
- [x] Sim boots, emits signals, fills, journals → check `reports/live_sim_analysis.md` exists
- [x] Firm skill mounted + plugin loaded → check `firm/templates/` dir exists
- [x] Firm bridge probe (CONTRACT validated) → check `reports/firm_integration.json` has `ready: true`
- [x] Runtime shim auto-generated → check `src/mnq/firm_runtime.py` exists
- [x] live_sim end-to-end pass → check `live_sim` stage OK in run_all_phases
- [ ] Watchdog / heartbeat wiring → not yet implemented

## Phase 1 — Harden Foundation (target: 70%)
- [x] Structured logging + WAL SQLite journal → check `src/mnq/storage/journal.py` exists
- [x] Crash recovery test → check `crash_recovery` stage OK AND `reports/crash_recovery.md` exists
- [x] Daily digest generation → check `daily_digest` stage OK AND `reports/daily/` has today's file
- [x] Strategy graveyard + bug journal → check `reports/strategy_registry.md` has graveyard section
- [ ] 72h unattended burn-in → requires scheduled run, not yet done

## Phase 2 — Event Log & Replay (target: 100%)
- [x] SQLite journal with typed events → check `src/mnq/storage/` exists
- [x] Determinism replay harness → check `replay_journal` stage OK
- [x] Backtest/live parity checksum → built into replay_journal

## Phase 3 — Fill Documented Gaps (target: 90%)
- [x] Calibration (Brier / log-loss / LOOCV) → check `calibration` stage OK
- [x] Firm-vs-baseline backtest → check `firm_vs_baseline` stage OK
- [x] Auto post-mortem generation → check `postmortem` stage OK
- [x] Firm review — markdown path → check `firm_review_markdown` stage OK
- [x] Firm review — LIVE Python agents → check `firm_live_review` stage OK
- [x] Strategy A/B harness → check `strategy_ab` stage OK
- [x] Pre-mortem template wiring → check `firm/templates/falsification.md` exists OR firm_review generates it
- [ ] Gauntlet gate implementation (12 gates) → check `src/mnq/gauntlet/` for non-stub implementations

## Phase 4 — Backtest / Live Parity (target: 60%)
- [x] summarize_env parity shape → exists in sim code
- [x] Replay journal determinism assertions → part of replay_journal
- [ ] Tolerance harness (paper vs shadow) → not yet built

## Phase 5 — Advanced Risk (target: 75%)
- [x] Kelly fraction with shrinkage → computed in firm_review and firm_live_review
- [x] Bayesian expectancy (Beta posteriors) → check `bayesian_expectancy` stage OK
- [x] Heat / concurrency budget per regime → heat_cap column in bayesian_expectancy report
- [ ] Full risk manager integration → risk agent exists in Firm but not wired to executor

## Phase 6 — API Boundary / VPS (blocked)
- [ ] VPS provisioning
- [ ] Docker deployment
- [ ] API boundary / auth
- [ ] Chicago VPN for low-latency

## Phase 7 — Real Broker (blocked, partial)
- [x] Tradovate REST client → `src/mnq/venues/tradovate/rest.py` exists
- [x] Tradovate WebSocket client → `src/mnq/venues/tradovate/ws.py` exists
- [x] Auth token lifecycle → `src/mnq/venues/tradovate/auth.py` exists
- [ ] Live order routing
- [ ] Position reconciliation

## Phase 8 — Shadow Trading (blocked)
- [ ] Live quote feed integration
- [ ] Shadow order matching
- [ ] 90-day validation period
- [ ] Drift monitoring

## Phase 9 — Tiered Live (blocked)
- [ ] Human approval gate
- [ ] Micro lot sizing
- [ ] Scale-up ladder
- [ ] 24/7 monitoring

## Cross-cutting
- [x] Strategy registry / hash tracking → `strategy_registry` stage OK
- [x] Walk-forward optimizer → `walk_forward` stage OK
- [x] Strategy graveyard file → part of registry
- [ ] Dashboard unification (mnq_bot + Firm)
- [ ] Paid data feed integration (IBKR/Databento)
