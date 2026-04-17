# GBM Filter · 2026-04-17 01:39:40 UTC

- train: **25** · test: **12**
- feature set: [bias, hour, weekday, qty, duration_s, is_long]
- weights: [0.0682, 0.0682, 0.2728, 0.0682, 0.0123, -0.6836]

## Test performance
- accuracy: **66.7%**
- precision: **66.7%**
- recall: **100.0%**
- confusion: TP=8 FP=4 FN=0 TN=0

_Minimal logistic regression — swap in sklearn/lightgbm when Phase C features land._
