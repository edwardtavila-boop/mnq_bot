# Changelog

All notable changes to `mnq_bot` are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses semantic versioning.

The pre-v0.2.3 history (initial commit through v0.2.2) is in git
log -- this changelog starts with the Red Team review closures from
2026-04-25.

## [v0.2.11] -- 2026-04-26

### Added

- `scripts/run_eta_live.py --inspect` diagnostic mode that prints
  the full `spec_payload` (JSON) plus the most recent tape bar plus
  the PM-stage Firm verdict for that bar, then exits without entering
  the tick loop. Useful for paper-soak debugging ("what is the runtime
  actually going to send to the Firm and what verdict comes back?")
- `tests/level_1_unit/test_run_eta_live_inspect.py` (8 tests) pinning
  the inspect contract: prints spec_payload, prints bar when tape
  configured, prints "none" when no tape, respects `--no-firm-review`,
  surfaces verdict when shim available, fail-open on shim ImportError,
  does not update TickStats (read-only diagnostic), produces parseable
  JSON for the spec block.

## [v0.2.10] -- 2026-04-26

### Changed

- `mnq.spec.runtime_payload._derive_sample_stats` now derives the
  trades-per-day rate from the live_sim journal
  (`_journal_trades_per_day`) instead of the hardcoded
  `TRADES_PER_DAY_PROXY = 2` constant. Falls back to the proxy when
  the journal is missing, empty, or unreadable. This closes the
  v0.2.7-deferred calibration item.

### Added

- `tests/level_1_unit/test_runtime_payload_journal_rate.py` (7 tests)
  pinning the journal-rate contract.

## [v0.2.9] -- 2026-04-26

### Added

- `OrderBook.submit()` now calls `assert_symbol_in_repo_scope(symbol)`
  before evaluating the gate chain. MBT/MET/spot-crypto symbols
  raise `WrongRepoSymbolError`, get journaled with
  `wrong_repo_symbol=True`, and never reach a broker. Makes the
  v0.2.8 guard live, not decorative.
- `tests/level_1_unit/test_orders_repo_scope_guard.py` (10 tests)
  pinning the live-wiring contract -- including a spy-chain test that
  verifies the wrong-repo path SHORT-CIRCUITS the gate chain.

## [v0.2.8] -- 2026-04-26

### Added

- `src/mnq/venues/repo_scope.py` -- repo-scope guard module.
  `ETA_ENGINE_SYMBOLS` lists CME micro crypto futures (MBT, MET,
  H/M/U/Z quarterly rolls) plus spot crypto cross-references.
  `WrongRepoSymbolError` is raised when a layer-3 symbol enters the
  mnq_bot venue layer; error text redirects to
  `eta_engine/venues/cme_micro_crypto.py`. Pins the locked
  two-project decision (CLAUDE.md 2026-04-17) structurally.
- `tests/level_1_unit/test_venues_repo_scope.py` (18 tests).

## [v0.2.7] -- 2026-04-26

### Changed

- `ApexRuntime._run_firm_review` now uses a real `spec_payload` built
  by `mnq.spec.runtime_payload.build_spec_payload(variant)` instead
  of a stub. The payload combines (a) variant `StrategyConfig` from
  `scripts/strategy_v2.VARIANTS`, (b) baseline yaml at
  `specs/strategies/v0_1_baseline.yaml`, (c) cached per-day P&L from
  `data/backtest_real_daily.json`. Each payload carries a
  `provenance` list so the PM agent and journal can see whether the
  values are calibrated or stub.

### Added

- `src/mnq/spec/runtime_payload.py`
- `tests/level_1_unit/test_spec_runtime_payload.py` (10 tests)

## [v0.2.6] -- 2026-04-26

### Added (B4 closure)

- `src/mnq/tape/databento_tape.py` -- streaming reader for the
  canonical Databento 5m MNQ tape. `iter_databento_bars` yields
  `Bar` objects in chronological order; RTH filter on by default.
- `ApexRuntime` now wires the tape into the tick loop and runs
  `firm_runtime.run_six_stage_review` per-bar. PM REJECT verdicts
  block the order intent and increment `firm_rejected` /
  `orders_blocked`.
- `scripts/firm_live_review.py` now uses a real-tape bar (last 80
  RTH bars, indicator state warmed up) instead of a hardcoded
  synthetic bar. Falls back to the legacy synthetic bar when the
  tape is missing.
- New CLI flags: `--tape PATH`, `--no-tape`, `--firm-review-every N`,
  `--no-firm-review`.
- `tests/level_1_unit/test_databento_tape.py` (10 tests) and
  `tests/level_1_unit/test_run_eta_live_b4.py` (9 tests).

### Fixed

- `OrderBook.allow_trade` kwarg was incorrectly named `now_utc=`;
  changed to `now=` to match the actual signature (would have
  crashed at T1+ rollout in v0.2.5 even though tests passed).

## [v0.2.5] -- 2026-04-26

### Added (B1 closure)

- `scripts/run_eta_live.py` -- live runtime entrypoint. Mirrors
  the eta_engine pattern. Refuse-to-boot guards:
  `kill_switch_latch` (always), `live_ready_env` /
  `broker_dormancy` / `promotion_gates` / `doctor` (live-only).
  Returns EX_BOOT_REFUSED=78 when any guard fails.
- `ApexRuntime` async tick loop with stop-event drain, signal
  handlers, and the full safety stack
  (`OrderBook(journal, build_default_chain())` +
  `CircuitBreaker(kill_switch=KillSwitchFile)` +
  `TieredRollout` from `RolloutStore`).

## [v0.2.4] -- 2026-04-26

### Added (H4 closure)

- `scripts/_promotion_gate.py` -- 9-gate live-promotion enforcement.
  Each gate has a deterministic PASS/FAIL/NO_DATA verdict. Aggregate
  exit code is 0 ONLY when every gate passes. Architectural pin:
  no `--override` / `--force` / `--skip` / `--no-fail` /
  `--advisory` flag exists; failure is structural.
- `tests/level_1_unit/test_promotion_gate.py` (9 tests) including
  the architectural pin against future override-flag additions.
- `scripts/run_all_phases.py` now runs the 9 gates as individual
  non-blocking stages plus a final blocking `promotion_verdict`
  aggregator.

## [v0.2.3] -- 2026-04-26

### Changed (B3 closure -- BREAKING)

- `OrderBook(journal, gate_chain)` requires `gate_chain` as a
  positional argument. Calling with `OrderBook(journal)` (the v0.2.2
  signature) raises `TypeError`. Tests that need the ungated path
  use `OrderBook.unsafe_no_gate_chain(journal)`.
- All 57 production callers migrated. `live_sim.py` and
  `shadow_trader.py` now use `build_default_chain()`.
- `from_journal` (read-only replay path) uses
  `unsafe_no_gate_chain` since it never calls `submit`.

## Pre-v0.2.3

Initial commit through v0.2.2 -- see `git log` for details. Highlights:
* v0.2.2: cross-cutting cleanup (A1 path centralization, A4
  firm-out-of-OneDrive, A5 cross-repo dormancy sync test)
* v0.2.1: full Red Team review (`bff9108`); BLOCKED with 5B + 4H
  findings; mechanical B5 + H1 closures
* v0.2.0: EVOLUTIONARY TRADING ALGO v3 framework initial commit (`d6ff6cb`)
