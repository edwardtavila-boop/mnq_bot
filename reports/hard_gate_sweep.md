# Gauntlet Hard-Gate Threshold Sweep — 15 days

Batch 9B. Sweeps gauntlet hard-gate skip/reduce thresholds.
Baseline: (0.00, 0.00) = no filtering.

| Skip | Reduce | PnL | Trades | Full | Reduced | Skip | Block% | Avg PnL/trade |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 0.00 | $+11.50 | 8 | 15 | 0 | 0 | 0.0% | $+1.44 |
| 0.25 | 0.50 | $+11.50 | 8 | 15 | 0 | 0 | 0.0% | $+1.44 |
| 0.33 | 0.50 | $+11.50 | 8 | 15 | 0 | 0 | 0.0% | $+1.44 |
| 0.40 | 0.60 | $+11.50 | 8 | 15 | 0 | 0 | 0.0% | $+1.44 |
| 0.50 | 0.67 | $+11.50 | 8 | 14 | 1 | 0 | 0.0% | $+1.44 |
| 0.50 | 0.75 | $+11.50 | 8 | 14 | 1 | 0 | 0.0% | $+1.44 |
| 0.60 | 0.75 | $+11.50 | 8 | 14 | 1 | 0 | 0.0% | $+1.44 |
| 0.67 | 0.83 | $+34.00 | 7 | 12 | 2 | 1 | 6.7% | $+4.86 ★ |
| 0.75 | 0.90 | $+34.00 | 7 | 6 | 8 | 1 | 6.7% | $+4.86 |

**Best config:** skip=0.67, reduce=0.83
- PnL: $+34.00 (baseline: $+11.50, Δ=$+22.50)
- Block rate: 6.7%
- Avg PnL/trade: $+4.86

## Interpretation

The hard-gate at skip=0.67/reduce=0.83 improved PnL by $+22.50 over unfiltered baseline. This confirms the gauntlet adds filtering value when applied as a direct pass/fail gate rather than via the delta-blend path.

_Sample: 15 Databento RTH days._
