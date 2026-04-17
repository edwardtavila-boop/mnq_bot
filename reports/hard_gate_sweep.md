# Gauntlet Hard-Gate Threshold Sweep — 200 days

Batch 9B. Sweeps gauntlet hard-gate skip/reduce thresholds.
Baseline: (0.00, 0.00) = no filtering.

| Skip | Reduce | PnL | Trades | Full | Reduced | Skip | Block% | Avg PnL/trade |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 0.00 | $+152.50 | 22 | 200 | 0 | 0 | 0.0% | $+6.93 ★ |
| 0.25 | 0.50 | $+152.50 | 22 | 200 | 0 | 0 | 0.0% | $+6.93 |
| 0.33 | 0.50 | $+152.50 | 22 | 200 | 0 | 0 | 0.0% | $+6.93 |
| 0.40 | 0.60 | $+152.50 | 22 | 198 | 2 | 0 | 0.0% | $+6.93 |
| 0.50 | 0.67 | $+152.50 | 22 | 178 | 22 | 0 | 0.0% | $+6.93 |
| 0.50 | 0.75 | $+152.50 | 22 | 178 | 22 | 0 | 0.0% | $+6.93 |
| 0.60 | 0.75 | $+152.50 | 22 | 178 | 20 | 2 | 1.0% | $+6.93 |
| 0.67 | 0.83 | $+124.50 | 20 | 123 | 55 | 22 | 11.0% | $+6.22 |
| 0.75 | 0.90 | $+124.50 | 20 | 71 | 107 | 22 | 11.0% | $+6.22 |

**Best config:** skip=0.00, reduce=0.00
- PnL: $+152.50 (baseline: $+152.50, Δ=$+0.00)
- Block rate: 0.0%
- Avg PnL/trade: $+6.93

## Interpretation

No threshold configuration improved PnL over the unfiltered baseline. The gauntlet pass_rate distribution may be too uniform (all days pass at similar rates), or the strategy's losses aren't concentrated on low-pass-rate days.

_Sample: 200 Databento RTH days._
