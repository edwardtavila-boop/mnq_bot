# Bayesian Expectancy + Heat Budget

- Prior: Beta(1.0, 1.0)
- Buckets evaluated: **9**

## Per-bucket posterior

| Regime | Side | n | W | L | Post. WR | 95% CI | Mean win | Mean loss | Post. exp | CI-lo exp | Heat cap |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| `trend_down` | long | 4 | 4 | 0 | 83.3% | 48.0% / 99.5% | $7.51 | $0.00 | $+6.26 | $+3.60 | 1 |
| `trend_up` | short | 3 | 3 | 0 | 80.0% | 39.8% / 99.2% | $7.59 | $0.00 | $+6.07 | $+3.02 | 1 |
| `trend_down` | short | 3 | 3 | 0 | 80.0% | 39.8% / 99.2% | $7.43 | $0.00 | $+5.94 | $+2.95 | 1 |
| `trend_up` | long | 2 | 2 | 0 | 75.0% | 29.2% / 99.2% | $7.26 | $0.00 | $+5.45 | $+2.12 | 1 |
| `range_bound` | long | 1 | 1 | 0 | 66.7% | 16.0% / 98.8% | $7.26 | $0.00 | $+4.84 | $+1.16 | 1 |
| `chop` | long | 8 | 6 | 2 | 70.0% | 40.0% / 92.8% | $7.01 | $7.49 | $+2.66 | $-1.69 | 0 |
| `chop` | short | 5 | 4 | 1 | 71.4% | 36.0% / 95.8% | $7.01 | $7.24 | $+2.94 | $-2.11 | 0 |
| `high_vol` | long | 8 | 2 | 6 | 30.0% | 7.5% / 60.2% | $6.51 | $7.66 | $-3.41 | $-6.59 | 0 |
| `high_vol` | short | 3 | 1 | 2 | 40.0% | 7.0% / 80.8% | $6.76 | $8.49 | $-2.39 | $-7.42 | 0 |

## Interpretation

* Heat-cap 0 buckets should be **blocked** by the gauntlet at runtime until more evidence flips their CI-lower positive.
* Posterior WR differs from empirical WR whenever n is small — that's the whole point of the Beta prior. On n=2 the posterior is pulled toward the prior mean by a lot; on n=30 the data dominate.
* CI-lower expectancy is the right number to size from. Kelly applied to the *point* expectancy over-commits on small-n buckets.
