# Per-Gate PnL Attribution — Outcome-Weighted Recalibration

Sample: 200 days, total PnL: $+152.50
Method: pearson_clamp

## Per-gate outcome weights

| Gate | Weight | Correlation | Pass→PnL | Fail→PnL | Pass N | Fail N | IV |
|---|---:|---:|---:|---:|---:|---:|---:|
| cross_mag | 0.073 | +0.073 | $+0.92 | $-2.25 | 190 | 10 | 0.335 |
| session | 0.019 | +0.019 | $+0.80 | $+0.00 | 190 | 10 | 0.085 |
| vol_band | 0.009 | +0.009 | $+0.83 | $+0.66 | 123 | 77 | 0.018 |
| correlation | 0.000 | +0.000 | $+0.76 | $+0.00 | 200 | 0 | 0.000 |
| news_window | 0.000 | +0.000 | $+0.76 | $+0.00 | 200 | 0 | 0.000 |
| orderflow | 0.000 | -0.059 | $+0.16 | $+1.27 | 92 | 108 | 0.117 |
| regime | 0.000 | -0.083 | $-0.04 | $+1.53 | 98 | 102 | 0.167 |
| spread | 0.000 | +0.000 | $+0.76 | $+0.00 | 200 | 0 | 0.000 |
| streak | 0.000 | +0.000 | $+0.76 | $+0.00 | 200 | 0 | 0.000 |
| time_of_day | 0.000 | +0.000 | $+0.77 | $+0.00 | 197 | 3 | 0.000 |
| trend_align | 0.000 | -0.155 | $-0.58 | $+2.37 | 109 | 91 | 0.312 |
| volume_confirm | 0.000 | +0.000 | $+0.76 | $+0.00 | 200 | 0 | 0.000 |

## Gate classification

**Value-adding** (positive PnL correlation, weight > 0.05): cross_mag

**Neutral** (weak positive correlation, weight ≤ 0.05): session, vol_band

**Value-destroying** (anti-correlated with PnL): orderflow, regime, trend_align

**Insufficient data** (< 5 failures, can't evaluate): correlation, news_window, spread, streak, time_of_day, volume_confirm

## Filtering comparison: raw vs outcome-weighted

Thresholds: skip=0.5, reduce=0.67

### Raw pass_rate filtering

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 178 | $+124.50 | $+0.70 |
| Reduced | 22 | $+28.00 | $+1.27 |
| Skipped | 0 | $+0.00 | $+0.00 |

### Outcome-weighted pass_rate filtering

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 190 | $+175.00 | $+0.92 |
| Reduced | 0 | $+0.00 | $+0.00 |
| Skipped | 10 | $-22.50 | $-2.25 |

### Effective PnL after filtering

- Raw filtering effective PnL: $+138.50 (lost $+14.00 to skip/reduce)
- OW filtering effective PnL: $+175.00 (lost $-22.50 to skip/reduce)
- Delta: $+36.50

## Interpretation

Outcome-weighted filtering outperforms raw filtering by $+36.50. The recalibration successfully redirects the gauntlet toward PnL-correlated signals.
