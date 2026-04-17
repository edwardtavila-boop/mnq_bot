# Rolling Calibration — stub (no data available) (60 trades)

- Epochs: **1**
- Drift alerts: **0**
- Brier range: [0.2500, 0.2500]
- Brier mean: 0.2500

## Epoch detail

| epoch | n | brier | log_loss | base_rate | mean_pred | z_brier | z_ll | drift |
|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| 0 | 60 | 0.2500 | 0.6931 | 0.500 | 0.500 | — | — |  |

## Interpretation

A drift alert means an epoch's Brier or log-loss deviates > 2σ from the running mean of prior epochs. This signals that the gauntlet gate scores may need recalibration — the relationship between predicted probabilities and realized outcomes has shifted.
