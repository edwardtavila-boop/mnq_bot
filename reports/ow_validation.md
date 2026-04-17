# OW Validation — Out-of-Sample Testing

Sample: 200 days

## Test 1: Chronological 60/40 Split

Train: 120 days (first 60%), Test: 80 days (last 40%)

### Weights learned from training set

| Gate | Weight | Correlation |
|---|---:|---:|
| cross_mag | 0.0896 | +0.0896 |
| session | 0.0239 | +0.0239 |
| orderflow | 0.0000 | -0.0754 |
| regime | 0.0000 | -0.1312 |
| trend_align | 0.0000 | -0.2133 |
| vol_band | 0.0000 | -0.0316 |

### Test set results (unseen data)

**Raw pass_rate**

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 64 | $+0.00 | $+0.00 |
| Reduced | 16 | $+0.00 | $+0.00 |
| Skipped | 0.0 | $+0.00 | $+0.00 |

Effective PnL: $+0.00 (total: $+0.00)

**Outcome-weighted**

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 78 | $+0.00 | $+0.00 |
| Reduced | 0 | $+0.00 | $+0.00 |
| Skipped | 2.0 | $+0.00 | $+0.00 |

Effective PnL: $+0.00 (total: $+0.00)

**OW vs Raw delta on test set: $+0.00**

## Test 2: Walk-Forward (rolling retrain)

Train window: 60 days, Test window: 30 days, Step: 30 days

| Fold | Train | Test | Raw Eff PnL | OW Eff PnL | Delta | Top OW Gate |
|---:|---|---|---:|---:|---:|---|
| 1 | 0–59 | 60–89 | $-53.00 | $-53.00 | $+0.00 | cross_mag (0.162) |
| 2 | 30–89 | 90–119 | $+0.00 | $+0.00 | $+0.00 | none |
| 3 | 60–119 | 120–149 | $+0.00 | $+0.00 | $+0.00 | none |
| 4 | 90–149 | 150–179 | $+0.00 | $+0.00 | $+0.00 | none |

**Walk-forward totals:** Raw $-53.00, OW $-53.00, Delta $+0.00

## Test 3: Jackknife Sensitivity

Drop each gate from the OW weight set (set to 0) and measure impact.

| Dropped Gate | Eff PnL | vs Full OW | Impact |
|---|---:|---:|---|
| cross_mag | $+152.50 | $-22.50 | HURTS |
| session | $+175.00 | $+0.00 | NEUTRAL |
| vol_band | $+175.00 | $+0.00 | NEUTRAL |

## Verdict

OW filtering does NOT outperform on out-of-sample data (split: $+0.00, WF: $+0.00). The in-sample improvement was likely overfitting. Keep raw pass_rate as the default gate until more data accumulates.
