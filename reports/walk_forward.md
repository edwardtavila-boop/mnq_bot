# Walk-Forward Optimizer Report

- train window: **8** days
- test window: **3** days
- stride: **1** day(s)
- folds: **5**

## Aggregate out-of-sample edge

- total test PnL across folds: **$+36.00**
- total test trades: **7**
- mean test PnL per fold: **$+7.20**
- stdev test PnL per fold: $27.24
- positive folds: **3/5**

## Per-fold ledger

| Fold | Train range | Test range | Train winner | Train PnL | Test PnL | Test n | Test WR |
|---:|---|---|---|---:|---:|---:|---:|
| 0 | 0–8 | 8–11 | `r5_real_wide_target` | $+14.50 | $+18.00 | 2 | 50.0% |
| 1 | 1–9 | 9–12 | `r5_real_wide_target` | $-26.50 | $+40.50 | 1 | 100.0% |
| 2 | 2–10 | 10–13 | `r5_real_wide_target` | $-26.50 | $+19.50 | 2 | 50.0% |
| 3 | 3–11 | 11–14 | `r5_real_wide_target` | $+36.50 | $-21.00 | 1 | 0.0% |
| 4 | 4–12 | 12–15 | `r5_real_wide_target` | $+36.50 | $-21.00 | 1 | 0.0% |

## Winner stability

| Variant | Fold wins |
|---|---:|
| `r5_real_wide_target` | 5 |

## Interpretation

* If the same variant wins most folds, the signal is stable — that's a candidate for Firm review and the falsification pipeline.
* If the fold winner rotates every window, either the tape is regime-heterogeneous (in which case we need a per-regime ensemble) or every variant is statistical noise.
* Out-of-sample mean per-fold PnL is the honest edge estimate. If it is negative or within one stdev of zero, no variant has earned the right to ship to shadow trading.
