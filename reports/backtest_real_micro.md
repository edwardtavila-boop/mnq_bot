# Batch 14 — Micro Entry Refinement — 2026-04-16 22:58 UTC

MicroEntryRefiner on V3 signals, Databento MNQ tape.
**1652 clean RTH days** (2019-05-06 → 2026-04-14)

## Variant Summary

| Variant | Trades | Micro✓ | Micro✗ | W | L | WR% | Total R | PF | MaxDD R | $ PnL | Avg µR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v3_baseline_pm30 | 192 | 0 | 192 | 100 | 29 | 52.1 | +20.19 | 1.70 | 10.15 | $+5,527.56 | 0.00x |
| v3_baseline_pm40 | 25 | 0 | 25 | 19 | 3 | 76.0 | +7.03 | 3.34 | 2.50 | $+3,432.28 | 0.00x |
| micro_strict_pm40 | 22 | 22 | 0 | 12 | 9 | 54.5 | +5.50 | 1.61 | 6.30 | $+102.92 | 38.42x |
| micro_fallback_pm40 | 25 | 22 | 3 | 14 | 10 | 56.0 | +5.50 | 1.55 | 7.00 | $+124.01 | 38.42x |
| micro_strict_pm30 | 97 | 97 | 0 | 59 | 37 | 60.8 | -6.02 | 0.85 | 15.20 | $-123.35 | 26.79x |
| micro_fallback_pm30 | 192 | 97 | 95 | 93 | 55 | 48.4 | -8.92 | 0.85 | 22.40 | $-2,735.41 | 26.79x |

## Micro Confirmation Analysis

**micro_strict_pm30:**
  - Confirmed: 97 trades, 61% WR, -6.02R
  - Unconfirmed: 0 trades, 0% WR, +0.00R

**micro_fallback_pm30:**
  - Confirmed: 97 trades, 61% WR, -6.02R
  - Unconfirmed: 95 trades, 36% WR, -2.89R

**micro_strict_pm40:**
  - Confirmed: 22 trades, 55% WR, +5.50R
  - Unconfirmed: 0 trades, 0% WR, +0.00R

**micro_fallback_pm40:**
  - Confirmed: 22 trades, 55% WR, +5.50R
  - Unconfirmed: 3 trades, 67% WR, +0.00R

## Verdict

**MICRO REFINEMENT NO LIFT** — strict micro at PM30 gives -26.21R vs baseline. Micro gating may be too aggressive.

*Generated in 75.9s*