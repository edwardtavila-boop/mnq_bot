# ScriptedStrategy v2 — A/B Report

- Data source: **real_mnq_1m_rth** (15 days)
- Variants tested: **3**
- Winner: **`r5_real_wide_target`**

## Ranked results

| # | Variant | Trades | Net PnL | 95% CI (boot) | Win% | Exp/trade |
|---:|---|---:|---:|---:|---:|---:|
| 1 | `r5_real_wide_target` ⭐ | 8 | $+11.50 | $-108.00 / $+136.50 | 37.5% | $+1.44 |
| 2 | `t16_r5_long_only` | 6 | $-6.00 | $-132.00 / $+140.00 | 33.3% | $-1.00 |
| 3 | `t17_r5_short_only` | 2 | $+17.50 | $-64.50 / $+117.00 | 50.0% | $+8.75 |

## Winner `r5_real_wide_target` — per-regime breakdown

| Regime | Trades | Wins | Win% | Net PnL |
|---|---:|---:|---:|---:|
| `real_chop` | 0 | 0 | 0.0% | $+0.00 |
| `real_high_vol` | 3 | 1 | 33.3% | $-4.00 |
| `real_trend_down` | 3 | 1 | 33.3% | $-4.00 |
| `real_trend_up` | 2 | 1 | 50.0% | $+19.50 |

## Winner `r5_real_wide_target` — per exit reason

| Exit reason | Trades | Net PnL |
|---|---:|---:|
| `stop` | 5 | $-108.00 |
| `take_profit` | 3 | $+119.50 |

## Winner `r5_real_wide_target` — per side

| Side | Trades | Net PnL |
|---|---:|---:|
| `long` | 6 | $-6.00 |
| `short` | 2 | $+17.50 |

## Daily PnL ladder (winner)

| Day | PnL | Trades |
|---:|---:|---:|
| 0 | $+18.50 | 2 |
| 1 | $+0.00 | 0 |
| 2 | $-22.50 | 1 |
| 3 | $+0.00 | 0 |
| 4 | $+0.00 | 0 |
| 5 | $+0.00 | 0 |
| 6 | $+18.50 | 2 |
| 7 | $+0.00 | 0 |
| 8 | $-22.50 | 1 |
| 9 | $+0.00 | 0 |
| 10 | $+40.50 | 1 |
| 11 | $+0.00 | 0 |
| 12 | $-21.00 | 1 |
| 13 | $+0.00 | 0 |
| 14 | $+0.00 | 0 |

## Interpretation

* Variants that show near-identical PnL to the baseline `v1_replica` are statistical noise — the filter added no edge (or cost us trades with no offsetting quality gain).
* A variant with materially higher expectancy AND at least ~15 trades has genuine lift; the bootstrap CI confirms sign stability.
* Variants that collapse trade count to <8 are under-selected — keep them only if they show >2x the baseline expectancy AND the CI excludes zero.
* The `real_high_vol` regime label comes from realized 1m-return stdev p75; anything above that bucket is our proxy for the `high_vol` regime that bled money in v1.