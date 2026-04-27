# Firm-Filtered vs. Baseline

- Days tested: **15**
- Filtered variant: `r5_real_wide_target`
- Baseline variant: `v1_replica`
- Apex V3 downstream gate: **ON**
- Apex snapshot source: **synthetic**

## Headline numbers

| Metric | Filtered | Baseline | Lift |
|---|---:|---:|---:|
| Trades | 8 | 47 | -39 |
| Net PnL | $-18.00 | $-138.00 | $+120.00 |
| Win rate | 37.5% | 27.7% | +9.8 pp |
| Expectancy / trade | $+1.44 | $-2.94 | $+4.37 |

## Daily lift (paired, filtered − baseline)

- Total lift: **$+120.00**
- 95% bootstrap CI: **$-2.50 / $+236.75**

## Apex V3 gate decisions

- Full: **11** | Reduced: **4** | Skipped: **0** (of 15 days)

## Per-day ledger

| Day | Filt PnL (raw) | Gate | Filt PnL (gated) | Baseline PnL | Lift |
|---:|---:|:---:|---:|---:|---:|
| 0 | $+18.50 | full (va=8) | $+18.50 | $-19.50 | $+38.00 |
| 1 | $+0.00 | full (va=13) | $+0.00 | $-30.50 | $+30.50 |
| 2 | $-22.50 | full (va=12) | $-22.50 | $+1.50 | $-24.00 |
| 3 | $+0.00 | full (va=6) | $+0.00 | $+3.00 | $-3.00 |
| 4 | $+0.00 | full (va=10) | $+0.00 | $-7.00 | $+7.00 |
| 5 | $+0.00 | full (va=6) | $+0.00 | $-7.00 | $+7.00 |
| 6 | $+18.50 | redu (va=4) | $+9.25 | $-8.50 | $+17.75 |
| 7 | $+0.00 | redu (va=4) | $+0.00 | $-5.50 | $+5.50 |
| 8 | $-22.50 | full (va=11) | $-22.50 | $-23.00 | $+0.50 |
| 9 | $+0.00 | full (va=13) | $+0.00 | $-19.50 | $+19.50 |
| 10 | $+40.50 | redu (va=3) | $+20.25 | $+18.00 | $+2.25 |
| 11 | $+0.00 | redu (va=2) | $+0.00 | $-6.50 | $+6.50 |
| 12 | $-21.00 | full (va=8) | $-21.00 | $-6.50 | $-14.50 |
| 13 | $+0.00 | full (va=10) | $+0.00 | $-5.50 | $+5.50 |
| 14 | $+0.00 | full (va=9) | $+0.00 | $-21.50 | $+21.50 |

## Verdict

**INCONCLUSIVE** — lift CI crosses zero. Nominal sign is positive; sample size or variance is the bottleneck. Collect more journal days or drop a gauntlet component with the lowest unique-contribution.

## Interpretation

* The Firm's accountability charter requires every complexity addition to the system to justify itself with a measurable lift. This report is the enforcement mechanism.
* A positive lift driven entirely by *fewer* trades (i.e. the filter avoids losers) is a different qualitative result than a positive lift driven by *equally many* trades with higher expectancy. Inspect the headline table.
* If the CI crosses zero, the Firm should specify a falsification test (deadline + effect size) in `reports/firm_reviews/` and commit to stripping the gauntlet if the test fails.
