# Gauntlet A/B Comparison

- Filtered variant: `r5_real_wide_target`
- Days: **15**

## Head-to-head

| Metric | Ungated | Gated | Δ |
|---|---:|---:|---:|
| Shadow PnL | $+11.50 | $+0.00 | $-11.50 |
| Trades | 8 | 0 | -8 |
| Fills | 16 | 0 | -16 |
| Avg PnL/trade | $+1.44 | $+0.00 | $-1.44 |
| Block rate | — | 100.0% | — |

## Gate failure distribution

| Gate | Failures | % of total |
|---|---:|---:|
| orderflow | 7 | 50.0% |
| regime | 5 | 35.7% |
| volume_confirm | 1 | 7.1% |
| cross_mag | 1 | 7.1% |

## Per-day comparison

| Day | Regime | Ungated PnL | Gated PnL | Δ PnL | G✓ | G✗ |
|---:|---|---:|---:|---:|---:|---:|
| 0 | real_trend_down | $+18.50 | $+0.00 | $-18.50 | 0 | 2 |
| 1 | real_high_vol | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |
| 2 | real_trend_down | $-22.50 | $+0.00 | $+22.50 | 0 | 1 |
| 3 | real_trend_down | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |
| 4 | real_trend_down | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |
| 5 | real_trend_down | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |
| 6 | real_high_vol | $+18.50 | $+0.00 | $-18.50 | 0 | 2 |
| 7 | real_trend_up | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |
| 8 | real_high_vol | $-22.50 | $+0.00 | $+22.50 | 0 | 1 |
| 9 | real_chop | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |
| 10 | real_trend_up | $+40.50 | $+0.00 | $-40.50 | 0 | 1 |
| 11 | real_trend_down | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |
| 12 | real_trend_up | $-21.00 | $+0.00 | $+21.00 | 0 | 1 |
| 13 | real_chop | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |
| 14 | real_trend_up | $+0.00 | $+0.00 | $+0.00 | 0 | 0 |

## Interpretation

The gauntlet **reduced** PnL by $-11.50 while blocking 8 trades (100.0% block rate). Some blocked trades were winners — review gate thresholds for over-filtering.
