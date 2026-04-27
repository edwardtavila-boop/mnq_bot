# OW Validation — Out-of-Sample Testing

Sample: 15 days

## Test 1: Chronological 60/40 Split

Train: 9 days (first 60%), Test: 6 days (last 40%)

### Weights learned from training set

| Gate | Weight | Correlation |
|---|---:|---:|

### Test set results (unseen data)

**Raw pass_rate**

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 6 | $+19.50 | $+3.25 |
| Reduced | 0 | $+0.00 | $+0.00 |
| Skipped | 0.0 | $+0.00 | $+0.00 |

Effective PnL: $+19.50 (total: $+19.50)

**Outcome-weighted**

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 0 | $+0.00 | $+0.00 |
| Reduced | 6 | $+19.50 | $+3.25 |
| Skipped | 0.0 | $+0.00 | $+0.00 |

Effective PnL: $+9.75 (total: $+19.50)

**OW vs Raw delta on test set: $-9.75**

## Test 2: Walk-Forward (rolling retrain)

Train window: 60 days, Test window: 30 days, Step: 30 days

| Fold | Train | Test | Raw Eff PnL | OW Eff PnL | Delta | Top OW Gate |
|---:|---|---|---:|---:|---:|---|

**Walk-forward totals:** Raw $+0.00, OW $+0.00, Delta $+0.00

## Test 3: Jackknife Sensitivity

Drop each gate from the OW weight set (set to 0) and measure impact.

| Dropped Gate | Eff PnL | vs Full OW | Impact |
|---|---:|---:|---|
| orderflow | $+5.75 | $-9.75 | HURTS |

## Verdict

OW filtering does NOT outperform on out-of-sample data (split: $-9.75, WF: $+0.00). The in-sample improvement was likely overfitting. Keep raw pass_rate as the default gate until more data accumulates.
