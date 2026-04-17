# Firm-Filtered vs. Baseline

- Days tested: **15**
- Filtered variant: `r5_real_wide_target`
- Baseline variant: `v1_replica`
- Apex V3 downstream gate: **off**

## Headline numbers

| Metric | Filtered | Baseline | Lift |
|---|---:|---:|---:|
| Trades | 8 | 47 | -39 |
| Net PnL | $+11.50 | $-138.00 | $+149.50 |
| Win rate | 37.5% | 27.7% | +9.8 pp |
| Expectancy / trade | $+1.44 | $-2.94 | $+4.37 |

## Daily lift (paired, filtered − baseline)

- Total lift: **$+149.50**
- 95% bootstrap CI: **$+23.50 / $+269.50**

## Per-day ledger

| Day | Filtered PnL | Baseline PnL | Lift |
|---:|---:|---:|---:|
| 0 | $+18.50 | $-19.50 | $+38.00 |
| 1 | $+0.00 | $-30.50 | $+30.50 |
| 2 | $-22.50 | $+1.50 | $-24.00 |
| 3 | $+0.00 | $+3.00 | $-3.00 |
| 4 | $+0.00 | $-7.00 | $+7.00 |
| 5 | $+0.00 | $-7.00 | $+7.00 |
| 6 | $+18.50 | $-8.50 | $+27.00 |
| 7 | $+0.00 | $-5.50 | $+5.50 |
| 8 | $-22.50 | $-23.00 | $+0.50 |
| 9 | $+0.00 | $-19.50 | $+19.50 |
| 10 | $+40.50 | $+18.00 | $+22.50 |
| 11 | $+0.00 | $-6.50 | $+6.50 |
| 12 | $-21.00 | $-6.50 | $-14.50 |
| 13 | $+0.00 | $-5.50 | $+5.50 |
| 14 | $+0.00 | $-21.50 | $+21.50 |

## Verdict

**FIRM FILTER JUSTIFIED** — lift CI strictly positive.

## Interpretation

* The Firm's accountability charter requires every complexity addition to the system to justify itself with a measurable lift. This report is the enforcement mechanism.
* A positive lift driven entirely by *fewer* trades (i.e. the filter avoids losers) is a different qualitative result than a positive lift driven by *equally many* trades with higher expectancy. Inspect the headline table.
* If the CI crosses zero, the Firm should specify a falsification test (deadline + effect size) in `reports/firm_reviews/` and commit to stripping the gauntlet if the test fails.
