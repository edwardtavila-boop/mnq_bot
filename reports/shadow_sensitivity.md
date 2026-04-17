# Shadow Venue Sensitivity Sweep

Batch 6C. Tests how PnL degrades under increasing execution friction.
Baseline: r5_real_wide_target variant, 15-day sample, real Apex V3 gate.

## Slippage Sensitivity

Latency = 0ms, Partial fill = 0%

| Ticks | PnL | Trades | Avg Slip $/trade | Max DD | Avg PnL/trade |
|---:|---:|---:|---:|---:|---:|
| 0.0 | $+11.50 (+0.00) | 8 | $0.00 | $64.50 | $+1.44 ★ |
| 0.5 | $+9.50 (-2.00) | 8 | $0.25 | $65.25 | $+1.19 |
| 1.0 | $+7.50 (-4.00) | 8 | $0.50 | $66.00 | $+0.94 |
| 2.0 | $+3.50 (-8.00) | 8 | $1.00 | $67.50 | $+0.44 |
| 4.0 | $-4.50 (-16.00) | 8 | $2.00 | $70.50 | $-0.56 |

## Latency Sensitivity

Slippage = 1 tick, Partial fill = 0%

| Latency ms | PnL | Trades | Max DD | Avg PnL/trade |
|---:|---:|---:|---:|---:|
| 0 | $+7.50 | 8 | $66.00 | $+0.94 |
| 25 | $+7.50 | 8 | $66.00 | $+0.94 |
| 50 | $+7.50 | 8 | $66.00 | $+0.94 |
| 100 | $+7.50 | 8 | $66.00 | $+0.94 |
| 250 | $+7.50 | 8 | $66.00 | $+0.94 |

## Partial Fill Sensitivity

Slippage = 1 tick, Latency = 50ms

| Partial % | PnL | Trades | Partials | Max DD | Avg PnL/trade |
|---:|---:|---:|---:|---:|---:|
| 0% | $+7.50 | 8 | 0 | $66.00 | $+0.94 |
| 5% | $+7.50 | 8 | 0 | $66.00 | $+0.94 |
| 10% | $+7.50 | 8 | 0 | $66.00 | $+0.94 |
| 25% | $+7.50 | 8 | 0 | $66.00 | $+0.94 |

## Worst-Case Combined

| Scenario | PnL | Trades | Partials | Rejections | Max DD | Avg PnL/trade |
|---|---:|---:|---:|---:|---:|---:|
| 4t slip + 250ms + 25% partial | $-4.50 | 8 | 0 | 0 | $70.50 | $-0.56 |

## Interpretation

1-tick slippage costs 35% of edge vs zero-slippage baseline. 
Worst-case friction turns the strategy negative — the edge is thin enough that 4-tick slippage erodes it completely. Real-world execution must be 1-tick or better.
