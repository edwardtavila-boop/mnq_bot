# Firm-Filtered vs. Baseline

- Days tested: **1**
- Filtered variant: `r5_real_wide_target`
- Baseline variant: `v1_replica`
- Apex V3 downstream gate: **off**

## Headline numbers

| Metric | Filtered | Baseline | Lift |
|---|---:|---:|---:|
| Trades | 1 | 3 | -2 |
| Net PnL | $-21.00 | $+8.50 | $-29.50 |
| Win rate | 0.0% | 66.7% | -66.7 pp |
| Expectancy / trade | $-21.00 | $+2.83 | $-23.83 |

## Daily lift (paired, filtered − baseline)

- Total lift: **$-29.50**
- 95% bootstrap CI: **$-29.50 / $-29.50**

## Per-day ledger

| Day | Filtered PnL | Baseline PnL | Lift |
|---:|---:|---:|---:|
| 0 | $-21.00 | $+8.50 | $-29.50 |

## Verdict

**FIRM FILTER HARMFUL** — lift CI strictly negative; review each gauntlet component.

## Interpretation

* The Firm's accountability charter requires every complexity addition to the system to justify itself with a measurable lift. This report is the enforcement mechanism.
* A positive lift driven entirely by *fewer* trades (i.e. the filter avoids losers) is a different qualitative result than a positive lift driven by *equally many* trades with higher expectancy. Inspect the headline table.
* If the CI crosses zero, the Firm should specify a falsification test (deadline + effect size) in `reports/firm_reviews/` and commit to stripping the gauntlet if the test fails.
