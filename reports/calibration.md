# ml_scorer — Calibration Report

- n = **37**
- base rate (empirical win rate) = **0.703**
- Brier score (in-sample) = **0.0876**
- Log-loss (in-sample) = **0.2959**
- Brier score (LOOCV) = **0.1495**
- Log-loss (LOOCV) = **0.4533**

## Reliability curve

| bucket pred-mean | realized win rate | n |
|---:|---:|---:|
| 0.110 | 0.000 | 4 |
| 0.285 | 0.333 | 3 |
| 0.499 | 0.333 | 6 |
| 0.724 | 0.833 | 6 |
| 0.911 | 1.000 | 18 |

## Interpretation

* Brier ≥ ``p*(1-p)`` (with p = base rate) means the scorer adds no information beyond the base rate — it is no better than always predicting the empirical win rate.
* A reliability curve that hugs the y=x line indicates good calibration. A curve that systematically over-promises (pred > realized) means the scorer is over-confident; under-promises means it is under-confident.
* LOOCV > in-sample by a wide margin is the classic overfit signature. When n is small (journals fresh off a 15-day run), treat LOOCV as the primary number and in-sample as a sanity check.
