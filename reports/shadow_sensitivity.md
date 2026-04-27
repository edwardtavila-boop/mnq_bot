# Shadow Venue Sensitivity Sweep

Batch 6C. Tests how PnL degrades under increasing execution friction.
Baseline: r5_real_wide_target variant, 15-day sample, real Apex V3 gate.

## Slippage Sensitivity

Latency = 0ms, Partial fill = 0%

| Ticks | PnL | Trades | Avg Slip $/trade | Max DD | Avg PnL/trade |
|---:|---:|---:|---:|---:|---:|
| 0.0 | $-21.00 (+0.00) | 1 | $0.00 | $21.00 | $-21.00 ★ |
| 0.5 | $-21.25 (-0.25) | 1 | $0.25 | $21.25 | $-21.25 |
| 1.0 | $-21.50 (-0.50) | 1 | $0.50 | $21.50 | $-21.50 |
| 2.0 | $-22.00 (-1.00) | 1 | $1.00 | $22.00 | $-22.00 |
| 4.0 | $-23.00 (-2.00) | 1 | $2.00 | $23.00 | $-23.00 |

## Latency Sensitivity

Slippage = 1 tick, Partial fill = 0%

| Latency ms | PnL | Trades | Max DD | Avg PnL/trade |
|---:|---:|---:|---:|---:|
| 0 | $-21.50 | 1 | $21.50 | $-21.50 |
| 25 | $-21.50 | 1 | $21.50 | $-21.50 |
| 50 | $-21.50 | 1 | $21.50 | $-21.50 |
| 100 | $-21.50 | 1 | $21.50 | $-21.50 |
| 250 | $-21.50 | 1 | $21.50 | $-21.50 |

## Partial Fill Sensitivity

Slippage = 1 tick, Latency = 50ms

| Partial % | PnL | Trades | Partials | Max DD | Avg PnL/trade |
|---:|---:|---:|---:|---:|---:|
| 0% | $-21.50 | 1 | 0 | $21.50 | $-21.50 |
| 5% | $-21.50 | 1 | 0 | $21.50 | $-21.50 |
| 10% | $-21.50 | 1 | 0 | $21.50 | $-21.50 |
| 25% | $-21.50 | 1 | 0 | $21.50 | $-21.50 |

## Worst-Case Combined

| Scenario | PnL | Trades | Partials | Rejections | Max DD | Avg PnL/trade |
|---|---:|---:|---:|---:|---:|---:|
| 4t slip + 250ms + 25% partial | $-23.00 | 1 | 0 | 0 | $23.00 | $-23.00 |

## Interpretation

1-tick slippage costs 2% of edge vs zero-slippage baseline. 
Worst-case friction turns the strategy negative — the edge is thin enough that 4-tick slippage erodes it completely. Real-world execution must be 1-tick or better.
