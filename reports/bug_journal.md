# Bug Journal

Last updated: 2026-04-24

Known bugs, workarounds, and resolutions. Ordered newest-first.

## Active

### BUG-010: `shadow_trader.py` arg parser rejects `--gauntlet`, `--v16`, `--output`
- **Discovered**: 2026-04-24 (firm-daily-orchestrator run).
- **Symptom**: Stages `gauntlet_shadow` (rc=2) and `shadow_v16` (rc=2) exit with `error: unrecognized arguments: --gauntlet --output reports/...`.
- **Root cause**: Orchestrator invokes `shadow_trader.py` with flags the current script's argparse doesn't define. Either the flags were renamed/removed or the orchestrator `cmd` list is stale vs. the script.
- **Impact**: MEDIUM — 2 Phase 8 shadow stages missing from daily run; shadow-venue parity checks blind.
- **Next action**: Align argparse definition in `scripts/shadow_trader.py` with the orchestrator's expectations, or update `_PHASE_8_STAGES` in `run_all_phases.py` to use the current CLI surface.
- **Status**: Open.

### BUG-009: `gauntlet_stats` ImportError on `DaySummary`
- **Discovered**: 2026-04-24 (firm-daily-orchestrator run).
- **Symptom**: `ImportError: cannot import name 'DaySummary' from 'shadow_trader'`.
- **Root cause**: `DaySummary` (and siblings it imports alongside) were removed/renamed in `shadow_trader.py`; `gauntlet_stats.py` still imports the old symbol.
- **Impact**: MEDIUM — Phase 8 gauntlet stats blind.
- **Next action**: Restore `DaySummary` symbol in `shadow_trader.py` OR refactor `gauntlet_stats.py` to pull the new equivalent.
- **Status**: Open.

### BUG-008: Orchestrator stages hit `FileNotFoundError` on missing Databento CSV
- **Discovered**: 2026-04-24 (firm-daily-orchestrator run).
- **Symptom**: 6 stages (`gauntlet_weight_sweep_full`, `hard_gate_sweep`, `hard_gate_attribution`, `gate_pnl_attribution`, `ow_validation`, `backtest_real`) raise `FileNotFoundError: Databento CSV not found: C:\mnq_data\databento\mnq1_1m.csv`.
- **Root cause**: Scripts use `real_bars.load_databento_days()` which raises hard when the cache is absent. Databento pulls are CANCELLED & DORMANT by operator mandate (2026-04-23); missing cache is the canonical state, not an error.
- **Impact**: LOW — by design; but surfaces as stage failures every day and pollutes the failure totals.
- **Fix required**: Make the orchestrator treat a missing Databento cache as a *SKIP* status (not FAIL) or short-circuit these stages in `run_all_phases.py` when `C:\mnq_data\databento\mnq1_1m.csv` is absent. Do NOT attempt to pull Databento.
- **Status**: Open.

### BUG-007: `walk_forward` stage needs ≥11 days, has 1
- **Discovered**: 2026-04-24 (firm-daily-orchestrator run).
- **Symptom**: `RuntimeError: not enough days: have 1, need >= 11` in `walk_forward.py:97`.
- **Root cause**: Walk-forward requires 10-day train + 1-day test window but the only source currently producing bars in the daily run yields 1 day.
- **Impact**: LOW — walk-forward is an optional cross-check; primary pipeline unaffected.
- **Next action**: Either widen source or gate walk_forward on `len(days) >= train_window+test_window` and mark as SKIP otherwise.
- **Status**: Open.

### BUG-006: 37 stages crash on Windows cp1252 codec
- **Discovered**: 2026-04-24 (firm-daily-orchestrator run).
- **Symptom**: `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'` (or `\u2212`, etc.) when stages print Unicode glyphs (→, −, ✓, ×) to stdout.
- **Root cause**: `subprocess.run` inherited parent env; Windows python defaults stdout to cp1252 which cannot encode common Unicode chars. 37 of 79 stages were affected — dominant failure class.
- **Fix (2026-04-24)**: `scripts/run_all_phases.py::_run` now injects `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` into the subprocess env and sets `encoding='utf-8', errors='replace'` on the subprocess.run call itself. Single-point fix; next orchestrator run should reclaim ~37 passes.
- **Status**: Resolved pending re-run verification.

### BUG-003: Shadow parity −$1.74/trade in realistic mode
- **Discovered**: 2026-04-14 (Batch 4C)
- **Symptom**: Deterministic mode is $0.00 diff, but stochastic slippage/latency introduces −$1.74 mean gap.
- **Root cause**: Expected — stochastic noise in fill prices diverges from zero-slippage sim.
- **Impact**: LOW — this IS the expected cost of realistic fills.
- **Workaround**: Track as a known friction cost. Sensitivity sweep (Batch 6C) quantifies the relationship.
- **Status**: By design. Not a bug, but documented here for visibility.

## Resolved

### BUG-004: V16 weight sweep flat across all samples
- **Discovered**: 2026-04-15 (Batch 5D), confirmed 2026-04-16 (Batch 8A)
- **Symptom**: All weights 0.00→0.40 produced identical PnL on both 15-day and 200-day samples.
- **Root cause**: PM base delta for GO verdicts is always positive (~+0.05 to +0.15). The gauntlet blend at max weight shifts delta by at most ±0.06 (`0.40 × gauntlet_delta × 0.15`). This never pushed a GO verdict's delta below the skip threshold (−0.10) or reduce threshold (−0.05). The gauntlet weight path was mathematically disconnected from the gate thresholds.
- **Fix (Batch 9A + 10A/B/C)**: Added `src/mnq/gauntlet/hard_gate.py` — independent gauntlet hard-gate that blocks trades by pass_rate, bypassing delta blending entirely. `combine_gates()` takes the stricter of `apex_gate` + `gauntlet_hard_gate`. Batch 10A/B/C layered outcome-weighted recalibration on top (`src/mnq/gauntlet/outcome_weights.py`) so the hard-gate uses gates that empirically correlate with PnL. V16 delta-blend weight remains at 0.15 (cosmetic) — the gauntlet's actual filtering power now flows through the hard-gate.
- **Status**: Resolved. Hard-gate shipped + outcome weights at `data/outcome_gate_weights.json`.

### BUG-005: Gauntlet gates anti-correlated with profitability
- **Discovered**: 2026-04-16 (Batch 9B)
- **Symptom**: Hard-gate attribution shows reduced days (pass_rate 0.50–0.67) have higher avg PnL ($1.27) than full days ($0.70). Blocking any days hurts total PnL.
- **Root cause**: Gate scores calibrated against "conditions suitable for trading," not actual profitability. The two are weakly anti-correlated — profitable trades happen in messy conditions.
- **Fix (Batch 10A/B/C)**: Built outcome-weighted gate recalibration (`src/mnq/gauntlet/outcome_weights.py`). Per-gate PnL attribution identified cross_mag as only value-adding gate (+0.073 correlation). Three gates anti-correlated: trend_align (−0.155), regime (−0.083), orderflow (−0.059). OW filtering yields +$36.50 over raw on 200-day sample. Hard-gate updated with `gate_weights` parameter.
- **Status**: Resolved. Weights at `data/outcome_gate_weights.json`.

### BUG-002: Short signal stop/TP ordering ValueError
- **Discovered**: 2026-04-15 (Batch 6C)
- **Symptom**: `ValueError: need tp < ref < stop` when shadow_sensitivity generates short signals.
- **Root cause**: Short signals need stop *above* ref_price and TP *below*. Original code used long-side ordering for both directions.
- **Fix**: Added conditional logic in `shadow_sensitivity.py` for `trade_side`.
- **Status**: Resolved.

### BUG-001: VolumeAwareSlippage test flake at low qty
- **Discovered**: 2026-04-15 (Batch 6C)
- **Symptom**: `test_different_seed_different_result` passes inconsistently at qty=5.
- **Root cause**: At qty=5 the noise amplitude is too small relative to tick_size — both seeds round to the same tick.
- **Fix**: Changed test to use qty=100 where noise is material.
- **Status**: Resolved.

## Journal Rules

1. Every bug gets an ID (BUG-NNN) and discovery date.
2. Include root cause analysis, not just the symptom.
3. "By design" entries are welcome — they prevent rediscovery.
4. Move to Resolved with the fix description when closed.
