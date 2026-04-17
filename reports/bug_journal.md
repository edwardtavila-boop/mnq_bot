# Bug Journal

Last updated: 2026-04-16

Known bugs, workarounds, and resolutions. Ordered newest-first.

## Active

### BUG-004: V16 weight sweep flat across all samples
- **Discovered**: 2026-04-15 (Batch 5D), confirmed 2026-04-16 (Batch 8A)
- **Symptom**: All weights 0.00→0.40 produce identical PnL on both 15-day and 200-day samples.
- **Root cause**: PM base delta for GO verdicts is always positive (~+0.05 to +0.15). The gauntlet blend at max weight shifts delta by at most ±0.06 (`0.40 × gauntlet_delta × 0.15`). This never pushes a GO verdict's delta below the skip threshold (−0.10) or reduce threshold (−0.05). The gauntlet weight path is mathematically disconnected from the gate thresholds.
- **Impact**: MEDIUM — the V16 voice injection architecture works but has no filtering power. The gauntlet's value must come from a different integration point (direct gate pass/fail, not delta blending).
- **Workaround**: Keep weight at 0.15 (zero risk). Future: add a gauntlet hard-gate that independently blocks trades when pass_rate < threshold, bypassing the delta blend entirely.
- **Status**: Confirmed by design. Next action: Batch 9+ add gauntlet hard-gate parallel to apex_gate.

### BUG-003: Shadow parity −$1.74/trade in realistic mode
- **Discovered**: 2026-04-14 (Batch 4C)
- **Symptom**: Deterministic mode is $0.00 diff, but stochastic slippage/latency introduces −$1.74 mean gap.
- **Root cause**: Expected — stochastic noise in fill prices diverges from zero-slippage sim.
- **Impact**: LOW — this IS the expected cost of realistic fills.
- **Workaround**: Track as a known friction cost. Sensitivity sweep (Batch 6C) quantifies the relationship.
- **Status**: By design. Not a bug, but documented here for visibility.

## Resolved

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
