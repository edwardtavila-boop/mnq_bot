# Per-Gate PnL Attribution — Outcome-Weighted Recalibration

Sample: 15 days, total PnL: $+11.50
Method: pearson_clamp

## Per-gate outcome weights

| Gate | Weight | Correlation | Pass→PnL | Fail→PnL | Pass N | Fail N | IV |
|---|---:|---:|---:|---:|---:|---:|---:|
| orderflow | 0.074 | +0.074 | $+1.72 | $-0.67 | 9 | 6 | 0.151 |
| correlation | 0.000 | +0.000 | $+0.77 | $+0.00 | 15 | 0 | 0.000 |
| cross_mag | 0.000 | +0.000 | $+0.82 | $+0.00 | 14 | 1 | 0.000 |
| news_window | 0.000 | +0.000 | $+0.77 | $+0.00 | 15 | 0 | 0.000 |
| regime | 0.000 | +0.000 | $+4.88 | $-0.73 | 4 | 11 | 0.000 |
| session | 0.000 | +0.000 | $-3.65 | $+29.50 | 13 | 2 | 0.000 |
| spread | 0.000 | +0.000 | $+0.77 | $+0.00 | 15 | 0 | 0.000 |
| streak | 0.000 | +0.000 | $+0.77 | $+0.00 | 15 | 0 | 0.000 |
| time_of_day | 0.000 | +0.000 | $+0.77 | $+0.00 | 15 | 0 | 0.000 |
| trend_align | 0.000 | +0.000 | $+2.83 | $-7.50 | 12 | 3 | 0.000 |
| vol_band | 0.000 | +0.000 | $+3.45 | $-6.62 | 11 | 4 | 0.000 |
| volume_confirm | 0.000 | +0.000 | $+0.77 | $+0.00 | 15 | 0 | 0.000 |

## Gate classification

**Value-adding** (positive PnL correlation, weight > 0.05): orderflow

**Insufficient data** (< 5 failures, can't evaluate): correlation, cross_mag, news_window, session, spread, streak, time_of_day, trend_align, vol_band, volume_confirm

## Filtering comparison: raw vs outcome-weighted

Thresholds: skip=0.5, reduce=0.67

### Raw pass_rate filtering

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 14 | $+34.00 | $+2.43 |
| Reduced | 1 | $-22.50 | $-22.50 |
| Skipped | 0 | $+0.00 | $+0.00 |

### Outcome-weighted pass_rate filtering

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 9 | $+15.50 | $+1.72 |
| Reduced | 0 | $+0.00 | $+0.00 |
| Skipped | 6 | $-4.00 | $-0.67 |

### Effective PnL after filtering

- Raw filtering effective PnL: $+22.75 (lost $-11.25 to skip/reduce)
- OW filtering effective PnL: $+15.50 (lost $-4.00 to skip/reduce)
- Delta: $-7.25

## Interpretation

Outcome-weighted filtering underperforms raw by $+7.25. This may indicate overfitting or that the gate-PnL relationship is noisy. Consider wider training window or different threshold levels.
