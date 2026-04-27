# Edge Forensics Report — 2026-04-18T12:12:00.644495+00:00

Decomposition of every backtest variant that produced ≥ 1 real trade. Five lenses: quarterly equity (4 buckets), regime expectancy, Deflated Sharpe (@30 and @100 multi-testing budget), 10,000-iter bootstrap CI, and transaction-cost sensitivity at $-1.74 / $-5 / $-10 per round trip.

## Verdict key

- **PASS** — no fatal findings, no warnings. Edge reproducible under cost stress.
- **WATCH** — one warning (e.g. Sharpe < 0.5, edge concentrated in early buckets, breakeven at measured shadow parity). Not fatal but fragile.
- **FRAGILE** — two or more warnings. Ship only with tight monitoring.
- **FAIL** — one fatal finding (negative PnL, CI covers zero, DSR < 0.5, PnL negative at $-5/trade).
- **KILL** — two or more fatal findings. Variant is not trading material.

## Headline table

| Variant | Trades | Days | Total $ | /trade | Win% | Sharpe | t-stat | DSR@30 | DSR@100 | CI-lo | CI-hi | Zero? | $@-1.74 | $@-5 | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---:|---:|:---:|
| `orb_only_pm30` | 112 | 112 | +11215.07 | +100.13 | 76.8% | 4.31 | +2.88 | 1.00 | 1.00 | +3326.45 | +18487.18 | no | +11020.19 | +10655.07 | **PASS** |
| `orb_regime_pm30` | 112 | 112 | +11215.07 | +100.13 | 76.8% | 4.31 | +2.88 | 1.00 | 1.00 | +3326.60 | +18641.26 | no | +11020.19 | +10655.07 | **PASS** |
| `orb_sweep_pm30` | 112 | 112 | +11215.07 | +100.13 | 76.8% | 4.31 | +2.88 | 1.00 | 1.00 | +3298.70 | +18695.58 | no | +11020.19 | +10655.07 | **PASS** |
| `orb_regime_conf_pm25` | 173 | 173 | +10594.24 | +61.24 | 72.8% | 4.38 | +3.63 | 1.00 | 1.00 | +4924.55 | +16199.21 | no | +10293.22 | +9729.24 | **PASS** |
| `orb_confidence_pm30` | 112 | 112 | +7125.48 | +63.62 | 76.8% | 4.11 | +2.74 | 1.00 | 1.00 | +2001.55 | +12045.71 | no | +6930.60 | +6565.48 | **PASS** |
| `orb_regime_conf_pm30` | 112 | 112 | +7125.48 | +63.62 | 76.8% | 4.11 | +2.74 | 1.00 | 1.00 | +1916.03 | +12208.88 | no | +6930.60 | +6565.48 | **PASS** |
| `v3_4_fib_partial_pm30` | 192 | 186 | +5527.56 | +28.79 | 52.1% | 1.41 | +1.21 | 0.00 | 0.00 | -3559.70 | +14318.12 | YES | +5193.48 | +4567.56 | **KILL** |
| `all_setups_pm30` | 192 | 186 | +5527.56 | +28.79 | 52.1% | 1.41 | +1.21 | 0.00 | 0.00 | -3399.58 | +14170.62 | YES | +5193.48 | +4567.56 | **KILL** |
| `v3_baseline_pm30` | 192 | 186 | +5527.56 | +28.79 | 52.1% | 1.41 | +1.21 | 0.00 | 0.00 | -3350.23 | +14147.89 | YES | +5193.48 | +4567.56 | **KILL** |
| `v3_0_fib_partial_pb` | 25 | 25 | +3432.27 | +137.29 | 76.0% | 5.49 | +1.73 | 0.00 | 0.00 | -573.61 | +7045.88 | YES | +3388.77 | +3307.27 | **KILL** |
| `v3_1_fib_partial_mkt` | 25 | 25 | +3432.27 | +137.29 | 76.0% | 5.49 | +1.73 | 0.00 | 0.00 | -529.72 | +7044.56 | YES | +3388.77 | +3307.27 | **KILL** |
| `v3_6_firm_only` | 25 | 25 | +3432.27 | +137.29 | 76.0% | 5.49 | +1.73 | 0.00 | 0.00 | -549.79 | +7038.01 | YES | +3388.77 | +3307.27 | **KILL** |
| `v3_7_fib_full_session` | 25 | 25 | +3432.27 | +137.29 | 76.0% | 5.49 | +1.73 | 0.00 | 0.00 | -520.92 | +7084.96 | YES | +3388.77 | +3307.27 | **KILL** |
| `v3_8_hybrid_exits` | 25 | 25 | +3432.27 | +137.29 | 76.0% | 5.49 | +1.73 | 0.00 | 0.00 | -569.79 | +7061.70 | YES | +3388.77 | +3307.27 | **KILL** |
| `v3_9_strong_redteam` | 25 | 25 | +3432.27 | +137.29 | 76.0% | 5.49 | +1.73 | 0.00 | 0.00 | -534.03 | +7062.61 | YES | +3388.77 | +3307.27 | **KILL** |
| `orb_only_pm40` | 25 | 25 | +3432.27 | +137.29 | 76.0% | 5.49 | +1.73 | 0.00 | 0.00 | -633.23 | +7056.49 | YES | +3388.77 | +3307.27 | **KILL** |
| `v3_baseline_pm40` | 25 | 25 | +3432.27 | +137.29 | 76.0% | 5.49 | +1.73 | 0.00 | 0.00 | -561.54 | +7092.00 | YES | +3388.77 | +3307.27 | **KILL** |
| `micro_fallback_pm40` | 25 | 25 | +124.01 | +4.96 | 56.0% | 0.34 | +0.11 | 0.00 | 0.00 | -2322.56 | +2350.50 | YES | +80.51 | -0.99 | **KILL** |
| `micro_strict_pm40` | 22 | 22 | +102.92 | +4.68 | 54.5% | 1.91 | +0.56 | 0.00 | 0.00 | -179.80 | +512.08 | YES | +64.64 | -7.08 | **KILL** |
| `n6_pct_adaptive_stop` | 30 | 30 | +54.50 | +1.82 | 33.3% | 0.34 | +0.12 | 0.00 | 0.00 | -845.50 | +1009.00 | YES | +2.30 | -95.50 | **KILL** |
| `r7_real_conviction` | 13 | 13 | -60.00 | -4.62 | 30.8% | -3.05 | -0.69 | 0.00 | 0.00 | -210.00 | +90.00 | YES | -82.62 | -125.00 | **KILL** |
| `micro_strict_pm30` | 97 | 96 | -123.35 | -1.27 | 60.8% | -0.76 | -0.47 | 0.00 | 0.00 | -606.33 | +424.87 | YES | -292.13 | -608.35 | **KILL** |
| `t4_r5_tight_cross` | 65 | 62 | -400.00 | -6.15 | 23.1% | -4.05 | -2.01 | 0.00 | 0.00 | -760.00 | +20.00 | YES | -513.10 | -725.00 | **KILL** |
| `n7_pct_adaptive_long` | 16 | 16 | -420.00 | -26.25 | 18.8% | -5.74 | -1.45 | 0.00 | 0.00 | -960.00 | +120.00 | YES | -447.84 | -500.00 | **KILL** |
| `n5_pct_long_morning` | 104 | 97 | -460.00 | -4.42 | 26.0% | -2.68 | -1.66 | 0.00 | 0.00 | -940.00 | +80.00 | YES | -640.96 | -980.00 | **KILL** |
| `t16_r5_long_only` | 112 | 101 | -585.00 | -5.22 | 25.0% | -3.46 | -2.19 | 0.00 | 0.00 | -1100.00 | -35.00 | no | -779.88 | -1145.00 | **KILL** |
| `t7_r5_morning_only` | 135 | 116 | -600.00 | -4.44 | 25.9% | -2.97 | -2.01 | 0.00 | 0.00 | -1200.00 | +0.00 | YES | -834.90 | -1275.00 | **KILL** |
| `t8_r5_afternoon_only` | 102 | 94 | -805.00 | -7.89 | 20.6% | -5.35 | -3.27 | 0.00 | 0.00 | -1260.00 | -325.00 | no | -982.48 | -1315.00 | **KILL** |
| `t17_r5_short_only` | 125 | 113 | -820.00 | -6.56 | 22.4% | -4.29 | -2.87 | 0.00 | 0.00 | -1360.00 | -280.00 | no | -1037.50 | -1445.00 | **KILL** |
| `t15_r5_pm_no_volcap` | 133 | 119 | -1005.00 | -7.56 | 21.1% | -5.17 | -3.55 | 0.00 | 0.00 | -1545.00 | -440.00 | no | -1236.42 | -1670.00 | **KILL** |
| `n2_pct_long_only` | 186 | 153 | -1045.00 | -5.62 | 24.2% | -3.73 | -2.90 | 0.00 | 0.00 | -1705.00 | -360.00 | no | -1368.64 | -1975.00 | **KILL** |
| `n3_pct_morning_only` | 206 | 160 | -1120.00 | -5.44 | 24.3% | -3.88 | -3.09 | 0.00 | 0.00 | -1840.00 | -400.00 | no | -1478.44 | -2150.00 | **KILL** |
| `t0_r5_tight_stop` | 237 | 188 | -1121.00 | -4.73 | 23.6% | -4.18 | -3.61 | 0.00 | 0.00 | -1697.07 | -497.00 | no | -1533.38 | -2306.00 | **KILL** |
| `t13_r5_pm_loose_cross` | 183 | 155 | -1165.00 | -6.37 | 23.0% | -4.35 | -3.41 | 0.00 | 0.00 | -1800.00 | -494.75 | no | -1483.42 | -2080.00 | **KILL** |
| `t12_r5_pm_wide` | 136 | 115 | -1185.00 | -8.71 | 19.1% | -6.30 | -4.26 | 0.00 | 0.00 | -1700.00 | -620.00 | no | -1421.64 | -1865.00 | **KILL** |
| `t6_r5_strict_flow` | 220 | 181 | -1245.00 | -5.66 | 24.1% | -3.96 | -3.36 | 0.00 | 0.00 | -1965.00 | -465.00 | no | -1627.80 | -2345.00 | **KILL** |
| `t2_r5_rr25` | 237 | 188 | -1345.00 | -5.68 | 20.7% | -3.54 | -3.05 | 0.00 | 0.00 | -2150.00 | -470.00 | no | -1757.38 | -2530.00 | **KILL** |
| `v3_2_rmult_partial_pb` | 25 | 25 | -1395.27 | -55.81 | 20.0% | -3.39 | -1.07 | 0.00 | 0.00 | -4110.36 | +793.24 | YES | -1438.77 | -1520.27 | **KILL** |
| `t9_r5_short_hold` | 237 | 188 | -1404.50 | -5.93 | 23.6% | -4.17 | -3.60 | 0.00 | 0.00 | -2160.00 | -644.50 | no | -1816.88 | -2589.50 | **KILL** |
| `r5_real_wide_target` | 237 | 188 | -1405.00 | -5.93 | 23.6% | -4.16 | -3.60 | 0.00 | 0.00 | -2150.00 | -625.00 | no | -1817.38 | -2590.00 | **KILL** |
| `t10_r5_long_hold` | 237 | 188 | -1405.00 | -5.93 | 23.6% | -4.16 | -3.60 | 0.00 | 0.00 | -2160.00 | -625.00 | no | -1817.38 | -2590.00 | **KILL** |
| `t11_r5_no_cooldown` | 237 | 188 | -1405.00 | -5.93 | 23.6% | -4.16 | -3.60 | 0.00 | 0.00 | -2160.00 | -625.00 | no | -1817.38 | -2590.00 | **KILL** |
| `t1_r5_wide_stop` | 237 | 188 | -1465.50 | -6.18 | 24.9% | -3.40 | -2.94 | 0.00 | 0.00 | -2370.15 | -529.50 | no | -1877.88 | -2650.50 | **KILL** |
| `r4_real_orderflow` | 237 | 188 | -1540.00 | -6.50 | 27.0% | -5.16 | -4.45 | 0.00 | 0.00 | -2190.00 | -890.00 | no | -1952.38 | -2725.00 | **KILL** |
| `t3_r5_rr175` | 237 | 188 | -1570.00 | -6.62 | 24.5% | -4.97 | -4.29 | 0.00 | 0.00 | -2250.00 | -855.00 | no | -1982.38 | -2755.00 | **KILL** |
| `t14_r5_pm_loose_flow` | 160 | 140 | -1665.00 | -10.41 | 16.2% | -7.70 | -5.74 | 0.00 | 0.00 | -2205.00 | -1115.00 | no | -1943.40 | -2465.00 | **KILL** |
| `r1_real_volfilter` | 178 | 151 | -1860.00 | -10.45 | 19.1% | -8.36 | -6.47 | 0.00 | 0.00 | -2360.00 | -1310.00 | no | -2169.72 | -2750.00 | **KILL** |
| `v3_3_fib_noptl_pb` | 25 | 25 | -2305.28 | -92.21 | 20.0% | -5.42 | -1.71 | 0.00 | 0.00 | -5092.66 | +78.69 | YES | -2348.78 | -2430.28 | **KILL** |
| `n0_pct_baseline` | 368 | 247 | -2525.00 | -6.86 | 22.0% | -5.17 | -5.11 | 0.00 | 0.00 | -3450.00 | -1565.00 | no | -3165.32 | -4365.00 | **KILL** |
| `micro_fallback_pm30` | 192 | 186 | -2735.38 | -14.25 | 48.4% | -0.90 | -0.77 | 0.00 | 0.00 | -9674.46 | +3911.76 | YES | -3069.46 | -3695.38 | **KILL** |
| `t5_r5_loose_flow` | 348 | 263 | -3145.00 | -9.04 | 18.4% | -6.87 | -7.02 | 0.00 | 0.00 | -3960.37 | -2295.00 | no | -3750.52 | -4885.00 | **KILL** |
| `r2_real_trend_morn` | 379 | 280 | -3730.00 | -9.84 | 20.3% | -8.57 | -9.03 | 0.00 | 0.00 | -4480.00 | -2930.00 | no | -4389.46 | -5625.00 | **KILL** |
| `r3_real_hard_pause` | 379 | 280 | -3730.00 | -9.84 | 20.3% | -8.57 | -9.03 | 0.00 | 0.00 | -4480.00 | -2930.00 | no | -4389.46 | -5625.00 | **KILL** |
| `n1_pct_loose` | 459 | 301 | -3865.00 | -8.42 | 19.4% | -6.52 | -7.13 | 0.00 | 0.00 | -4850.00 | -2870.00 | no | -4663.66 | -6160.00 | **KILL** |
| `r6_real_allday` | 836 | 638 | -5341.00 | -6.39 | 27.2% | -5.01 | -7.97 | 0.00 | 0.00 | -6567.50 | -4083.45 | no | -6795.64 | -9521.00 | **KILL** |
| `n4_pct_no_vol` | 8101 | 1145 | -131561.50 | -16.24 | 6.3% | -20.86 | -44.47 | 0.00 | 0.00 | -134118.08 | -129013.91 | no | -145657.24 | -172066.50 | **KILL** |
| `r0_real_baseline` | 12695 | 1100 | -213152.50 | -16.79 | 6.4% | -25.27 | -52.80 | 0.00 | 0.00 | -215793.52 | -210488.40 | no | -235241.80 | -276627.50 | **KILL** |

## Per-variant tearsheets

### `orb_only_pm30` — **PASS**

- Trades: 112 over 112 days · total $+11215.07 · avg $+100.1346/trade · win-rate 76.8%
- Sharpe: 4.31 · t-stat: +2.88 · DSR@30=1.00 · DSR@100=1.00
- Bootstrap 95% CI on total PnL: [$+3326.45, $+18487.18] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $+11020.19 ✓
    - @ $-5.00/trade → $+10655.07 ✓
    - @ $-10.00/trade → $+10095.07 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-04: $+3317.74 (+29.6% of total)
    - 2021-06-04..2023-01-01: $+4029.93 (+35.9% of total)
    - 2023-01-01..2024-07-30: $-2724.47 (-24.3% of total)
    - 2024-07-30..2026-02-26: $+6591.87 (+58.8% of total)
- Regime breakdown:
    - `RISK-ON`: $+8942.81 over 99 trades ($+90.33/trade)
    - `NEUTRAL`: $+2272.26 over 13 trades ($+174.79/trade)
- Findings: *(none — variant is clean under every lens)*

### `orb_regime_pm30` — **PASS**

- Trades: 112 over 112 days · total $+11215.07 · avg $+100.1346/trade · win-rate 76.8%
- Sharpe: 4.31 · t-stat: +2.88 · DSR@30=1.00 · DSR@100=1.00
- Bootstrap 95% CI on total PnL: [$+3326.60, $+18641.26] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $+11020.19 ✓
    - @ $-5.00/trade → $+10655.07 ✓
    - @ $-10.00/trade → $+10095.07 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-04: $+3317.74 (+29.6% of total)
    - 2021-06-04..2023-01-01: $+4029.93 (+35.9% of total)
    - 2023-01-01..2024-07-30: $-2724.47 (-24.3% of total)
    - 2024-07-30..2026-02-26: $+6591.87 (+58.8% of total)
- Regime breakdown:
    - `RISK-ON`: $+8942.81 over 99 trades ($+90.33/trade)
    - `NEUTRAL`: $+2272.26 over 13 trades ($+174.79/trade)
- Findings: *(none — variant is clean under every lens)*

### `orb_sweep_pm30` — **PASS**

- Trades: 112 over 112 days · total $+11215.07 · avg $+100.1346/trade · win-rate 76.8%
- Sharpe: 4.31 · t-stat: +2.88 · DSR@30=1.00 · DSR@100=1.00
- Bootstrap 95% CI on total PnL: [$+3298.70, $+18695.58] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $+11020.19 ✓
    - @ $-5.00/trade → $+10655.07 ✓
    - @ $-10.00/trade → $+10095.07 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-04: $+3317.74 (+29.6% of total)
    - 2021-06-04..2023-01-01: $+4029.93 (+35.9% of total)
    - 2023-01-01..2024-07-30: $-2724.47 (-24.3% of total)
    - 2024-07-30..2026-02-26: $+6591.87 (+58.8% of total)
- Regime breakdown:
    - `RISK-ON`: $+8942.81 over 99 trades ($+90.33/trade)
    - `NEUTRAL`: $+2272.26 over 13 trades ($+174.79/trade)
- Findings: *(none — variant is clean under every lens)*

### `orb_regime_conf_pm25` — **PASS**

- Trades: 173 over 173 days · total $+10594.24 · avg $+61.2384/trade · win-rate 72.8%
- Sharpe: 4.38 · t-stat: +3.63 · DSR@30=1.00 · DSR@100=1.00
- Bootstrap 95% CI on total PnL: [$+4924.55, $+16199.21] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $+10293.22 ✓
    - @ $-5.00/trade → $+9729.24 ✓
    - @ $-10.00/trade → $+8864.24 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-05: $+2463.54 (+23.3% of total)
    - 2021-06-05..2023-01-03: $+3024.77 (+28.6% of total)
    - 2023-01-03..2024-08-02: $-1096.13 (-10.3% of total)
    - 2024-08-02..2026-03-03: $+6202.06 (+58.5% of total)
- Regime breakdown:
    - `RISK-ON`: $+8979.36 over 152 trades ($+59.07/trade)
    - `NEUTRAL`: $+1614.88 over 21 trades ($+76.90/trade)
- Findings: *(none — variant is clean under every lens)*

### `orb_confidence_pm30` — **PASS**

- Trades: 112 over 112 days · total $+7125.48 · avg $+63.6204/trade · win-rate 76.8%
- Sharpe: 4.11 · t-stat: +2.74 · DSR@30=1.00 · DSR@100=1.00
- Bootstrap 95% CI on total PnL: [$+2001.55, $+12045.71] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $+6930.60 ✓
    - @ $-5.00/trade → $+6565.48 ✓
    - @ $-10.00/trade → $+6005.48 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-04: $+2173.77 (+30.5% of total)
    - 2021-06-04..2023-01-01: $+2399.99 (+33.7% of total)
    - 2023-01-01..2024-07-30: $-1824.44 (-25.6% of total)
    - 2024-07-30..2026-02-26: $+4376.16 (+61.4% of total)
- Regime breakdown:
    - `RISK-ON`: $+5727.85 over 99 trades ($+57.86/trade)
    - `NEUTRAL`: $+1397.63 over 13 trades ($+107.51/trade)
- Findings: *(none — variant is clean under every lens)*

### `orb_regime_conf_pm30` — **PASS**

- Trades: 112 over 112 days · total $+7125.48 · avg $+63.6204/trade · win-rate 76.8%
- Sharpe: 4.11 · t-stat: +2.74 · DSR@30=1.00 · DSR@100=1.00
- Bootstrap 95% CI on total PnL: [$+1916.03, $+12208.88] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $+6930.60 ✓
    - @ $-5.00/trade → $+6565.48 ✓
    - @ $-10.00/trade → $+6005.48 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-04: $+2173.77 (+30.5% of total)
    - 2021-06-04..2023-01-01: $+2399.99 (+33.7% of total)
    - 2023-01-01..2024-07-30: $-1824.44 (-25.6% of total)
    - 2024-07-30..2026-02-26: $+4376.16 (+61.4% of total)
- Regime breakdown:
    - `RISK-ON`: $+5727.85 over 99 trades ($+57.86/trade)
    - `NEUTRAL`: $+1397.63 over 13 trades ($+107.51/trade)
- Findings: *(none — variant is clean under every lens)*

### `v3_4_fib_partial_pm30` — **KILL**

- Trades: 192 over 186 days · total $+5527.56 · avg $+28.7894/trade · win-rate 52.1%
- Sharpe: 1.41 · t-stat: +1.21 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-3559.70, $+14318.12] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+5193.48 ✓
    - @ $-5.00/trade → $+4567.56 ✓
    - @ $-10.00/trade → $+3607.56 ✓
- Quarterly decomposition:
    - 2019-05-17..2021-01-27: $+2119.50 (+38.3% of total)
    - 2021-01-27..2022-10-10: $+3854.26 (+69.7% of total)
    - 2022-10-10..2024-06-22: $-4943.64 (-89.4% of total)
    - 2024-06-22..2026-03-05: $+4497.44 (+81.4% of total)
- Regime breakdown:
    - `RISK-ON`: $+3698.21 over 171 trades ($+21.63/trade)
    - `NEUTRAL`: $+1829.35 over 21 trades ($+87.11/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `all_setups_pm30` — **KILL**

- Trades: 192 over 186 days · total $+5527.56 · avg $+28.7894/trade · win-rate 52.1%
- Sharpe: 1.41 · t-stat: +1.21 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-3399.58, $+14170.62] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+5193.48 ✓
    - @ $-5.00/trade → $+4567.56 ✓
    - @ $-10.00/trade → $+3607.56 ✓
- Quarterly decomposition:
    - 2019-05-17..2021-01-27: $+2119.50 (+38.3% of total)
    - 2021-01-27..2022-10-10: $+3854.26 (+69.7% of total)
    - 2022-10-10..2024-06-22: $-4943.64 (-89.4% of total)
    - 2024-06-22..2026-03-05: $+4497.44 (+81.4% of total)
- Regime breakdown:
    - `RISK-ON`: $+3698.21 over 171 trades ($+21.63/trade)
    - `NEUTRAL`: $+1829.35 over 21 trades ($+87.11/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `v3_baseline_pm30` — **KILL**

- Trades: 192 over 186 days · total $+5527.56 · avg $+28.7894/trade · win-rate 52.1%
- Sharpe: 1.41 · t-stat: +1.21 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-3350.23, $+14147.89] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+5193.48 ✓
    - @ $-5.00/trade → $+4567.56 ✓
    - @ $-10.00/trade → $+3607.56 ✓
- Quarterly decomposition:
    - 2019-05-17..2021-01-27: $+2119.50 (+38.3% of total)
    - 2021-01-27..2022-10-10: $+3854.26 (+69.7% of total)
    - 2022-10-10..2024-06-22: $-4943.64 (-89.4% of total)
    - 2024-06-22..2026-03-05: $+4497.44 (+81.4% of total)
- Regime breakdown:
    - `RISK-ON`: $+3698.21 over 171 trades ($+21.63/trade)
    - `NEUTRAL`: $+1829.35 over 21 trades ($+87.11/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `v3_0_fib_partial_pb` — **KILL**

- Trades: 25 over 25 days · total $+3432.27 · avg $+137.2908/trade · win-rate 76.0%
- Sharpe: 5.49 · t-stat: +1.73 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-573.61, $+7045.88] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+3388.77 ✓
    - @ $-5.00/trade → $+3307.27 ✓
    - @ $-10.00/trade → $+3182.27 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+992.51 (+28.9% of total)
    - 2021-06-02..2022-12-29: $+976.31 (+28.4% of total)
    - 2022-12-29..2024-07-25: $-1600.30 (-46.6% of total)
    - 2024-07-25..2026-02-20: $+3063.75 (+89.3% of total)
- Regime breakdown:
    - `RISK-ON`: $+3231.11 over 24 trades ($+134.63/trade)
    - `NEUTRAL`: $+201.16 over 1 trades ($+201.16/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `v3_1_fib_partial_mkt` — **KILL**

- Trades: 25 over 25 days · total $+3432.27 · avg $+137.2908/trade · win-rate 76.0%
- Sharpe: 5.49 · t-stat: +1.73 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-529.72, $+7044.56] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+3388.77 ✓
    - @ $-5.00/trade → $+3307.27 ✓
    - @ $-10.00/trade → $+3182.27 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+992.51 (+28.9% of total)
    - 2021-06-02..2022-12-29: $+976.31 (+28.4% of total)
    - 2022-12-29..2024-07-25: $-1600.30 (-46.6% of total)
    - 2024-07-25..2026-02-20: $+3063.75 (+89.3% of total)
- Regime breakdown:
    - `RISK-ON`: $+3231.11 over 24 trades ($+134.63/trade)
    - `NEUTRAL`: $+201.16 over 1 trades ($+201.16/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `v3_6_firm_only` — **KILL**

- Trades: 25 over 25 days · total $+3432.27 · avg $+137.2908/trade · win-rate 76.0%
- Sharpe: 5.49 · t-stat: +1.73 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-549.79, $+7038.01] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+3388.77 ✓
    - @ $-5.00/trade → $+3307.27 ✓
    - @ $-10.00/trade → $+3182.27 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+992.51 (+28.9% of total)
    - 2021-06-02..2022-12-29: $+976.31 (+28.4% of total)
    - 2022-12-29..2024-07-25: $-1600.30 (-46.6% of total)
    - 2024-07-25..2026-02-20: $+3063.75 (+89.3% of total)
- Regime breakdown:
    - `RISK-ON`: $+3231.11 over 24 trades ($+134.63/trade)
    - `NEUTRAL`: $+201.16 over 1 trades ($+201.16/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `v3_7_fib_full_session` — **KILL**

- Trades: 25 over 25 days · total $+3432.27 · avg $+137.2908/trade · win-rate 76.0%
- Sharpe: 5.49 · t-stat: +1.73 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-520.92, $+7084.96] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+3388.77 ✓
    - @ $-5.00/trade → $+3307.27 ✓
    - @ $-10.00/trade → $+3182.27 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+992.51 (+28.9% of total)
    - 2021-06-02..2022-12-29: $+976.31 (+28.4% of total)
    - 2022-12-29..2024-07-25: $-1600.30 (-46.6% of total)
    - 2024-07-25..2026-02-20: $+3063.75 (+89.3% of total)
- Regime breakdown:
    - `RISK-ON`: $+3231.11 over 23 trades ($+140.48/trade)
    - `NEUTRAL`: $+201.16 over 2 trades ($+100.58/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `v3_8_hybrid_exits` — **KILL**

- Trades: 25 over 25 days · total $+3432.27 · avg $+137.2908/trade · win-rate 76.0%
- Sharpe: 5.49 · t-stat: +1.73 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-569.79, $+7061.70] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+3388.77 ✓
    - @ $-5.00/trade → $+3307.27 ✓
    - @ $-10.00/trade → $+3182.27 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+992.51 (+28.9% of total)
    - 2021-06-02..2022-12-29: $+976.31 (+28.4% of total)
    - 2022-12-29..2024-07-25: $-1600.30 (-46.6% of total)
    - 2024-07-25..2026-02-20: $+3063.75 (+89.3% of total)
- Regime breakdown:
    - `RISK-ON`: $+3231.11 over 24 trades ($+134.63/trade)
    - `NEUTRAL`: $+201.16 over 1 trades ($+201.16/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `v3_9_strong_redteam` — **KILL**

- Trades: 25 over 25 days · total $+3432.27 · avg $+137.2908/trade · win-rate 76.0%
- Sharpe: 5.49 · t-stat: +1.73 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-534.03, $+7062.61] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+3388.77 ✓
    - @ $-5.00/trade → $+3307.27 ✓
    - @ $-10.00/trade → $+3182.27 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+992.51 (+28.9% of total)
    - 2021-06-02..2022-12-29: $+976.31 (+28.4% of total)
    - 2022-12-29..2024-07-25: $-1600.30 (-46.6% of total)
    - 2024-07-25..2026-02-20: $+3063.75 (+89.3% of total)
- Regime breakdown:
    - `RISK-ON`: $+3231.11 over 24 trades ($+134.63/trade)
    - `NEUTRAL`: $+201.16 over 1 trades ($+201.16/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `orb_only_pm40` — **KILL**

- Trades: 25 over 25 days · total $+3432.27 · avg $+137.2908/trade · win-rate 76.0%
- Sharpe: 5.49 · t-stat: +1.73 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-633.23, $+7056.49] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+3388.77 ✓
    - @ $-5.00/trade → $+3307.27 ✓
    - @ $-10.00/trade → $+3182.27 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+992.51 (+28.9% of total)
    - 2021-06-02..2022-12-29: $+976.31 (+28.4% of total)
    - 2022-12-29..2024-07-25: $-1600.30 (-46.6% of total)
    - 2024-07-25..2026-02-20: $+3063.75 (+89.3% of total)
- Regime breakdown:
    - `RISK-ON`: $+3231.11 over 24 trades ($+134.63/trade)
    - `NEUTRAL`: $+201.16 over 1 trades ($+201.16/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `v3_baseline_pm40` — **KILL**

- Trades: 25 over 25 days · total $+3432.27 · avg $+137.2908/trade · win-rate 76.0%
- Sharpe: 5.49 · t-stat: +1.73 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-561.54, $+7092.00] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+3388.77 ✓
    - @ $-5.00/trade → $+3307.27 ✓
    - @ $-10.00/trade → $+3182.27 ✓
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+992.51 (+28.9% of total)
    - 2021-06-02..2022-12-29: $+976.31 (+28.4% of total)
    - 2022-12-29..2024-07-25: $-1600.30 (-46.6% of total)
    - 2024-07-25..2026-02-20: $+3063.75 (+89.3% of total)
- Regime breakdown:
    - `RISK-ON`: $+3231.11 over 24 trades ($+134.63/trade)
    - `NEUTRAL`: $+201.16 over 1 trades ($+201.16/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise

### `micro_fallback_pm40` — **KILL**

- Trades: 25 over 25 days · total $+124.01 · avg $+4.9604/trade · win-rate 56.0%
- Sharpe: 0.34 · t-stat: +0.11 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2322.56, $+2350.50] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+80.51 ✓
    - @ $-5.00/trade → $-0.99 ✗
    - @ $-10.00/trade → $-125.99 ✗
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+17.60 (+14.2% of total)
    - 2021-06-02..2022-12-29: $+176.07 (+142.0% of total)
    - 2022-12-29..2024-07-25: $-978.87 (-789.3% of total)
    - 2024-07-25..2026-02-20: $+909.21 (+733.2% of total)
- Regime breakdown:
    - `RISK-ON`: $+119.66 over 24 trades ($+4.99/trade)
    - `NEUTRAL`: $+4.35 over 1 trades ($+4.35/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction
    - Sharpe = 0.34 below 0.5 hurdle

### `micro_strict_pm40` — **KILL**

- Trades: 22 over 22 days · total $+102.92 · avg $+4.6782/trade · win-rate 54.5%
- Sharpe: 1.91 · t-stat: +0.56 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-179.80, $+512.08] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+64.64 ✓
    - @ $-5.00/trade → $-7.08 ✗
    - @ $-10.00/trade → $-117.08 ✗
- Quarterly decomposition:
    - 2019-11-06..2021-05-26: $+17.60 (+17.1% of total)
    - 2021-05-26..2022-12-14: $+43.60 (+42.4% of total)
    - 2022-12-14..2024-07-03: $-131.00 (-127.3% of total)
    - 2024-07-03..2026-01-21: $+172.72 (+167.8% of total)
- Regime breakdown:
    - `RISK-ON`: $+98.57 over 21 trades ($+4.69/trade)
    - `NEUTRAL`: $+4.35 over 1 trades ($+4.35/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `n6_pct_adaptive_stop` — **KILL**

- Trades: 30 over 30 days · total $+54.50 · avg $+1.8167/trade · win-rate 33.3%
- Sharpe: 0.34 · t-stat: +0.12 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-845.50, $+1009.00] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $+2.30 ✓
    - @ $-5.00/trade → $-95.50 ✗
    - @ $-10.00/trade → $-245.50 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-01-18: $+54.50 (+100.0% of total)
    - 2021-01-18..2022-09-17: $+180.00 (+330.3% of total)
    - 2022-09-17..2024-05-16: $+0.00 (+0.0% of total)
    - 2024-05-16..2026-01-13: $-180.00 (-330.3% of total)
- Regime breakdown:
    - `UNKNOWN`: $+54.50 over 30 trades ($+1.82/trade)
- Findings:
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction
    - edge concentrated in early buckets — last 1/4 of sample = $-180.00
    - Sharpe = 0.34 below 0.5 hurdle

### `r7_real_conviction` — **KILL**

- Trades: 13 over 13 days · total $-60.00 · avg $-4.6154/trade · win-rate 30.8%
- Sharpe: -3.05 · t-stat: -0.69 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-210.00, $+90.00] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-82.62 ✗
    - @ $-5.00/trade → $-125.00 ✗
    - @ $-10.00/trade → $-190.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-02-02: $-10.00 (+16.7% of total)
    - 2020-02-02..2020-10-16: $+30.00 (-50.0% of total)
    - 2020-10-16..2021-06-30: $+0.00 (-0.0% of total)
    - 2021-06-30..2022-03-14: $-80.00 (+133.3% of total)
- Regime breakdown:
    - `UNKNOWN`: $-60.00 over 13 trades ($-4.62/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction
    - edge concentrated in early buckets — last 1/4 of sample = $-80.00

### `micro_strict_pm30` — **KILL**

- Trades: 97 over 96 days · total $-123.35 · avg $-1.2716/trade · win-rate 60.8%
- Sharpe: -0.76 · t-stat: -0.47 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-606.33, $+424.87] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-292.13 ✗
    - @ $-5.00/trade → $-608.35 ✗
    - @ $-10.00/trade → $-1093.35 ✗
- Quarterly decomposition:
    - 2019-11-06..2021-06-04: $+167.06 (-135.4% of total)
    - 2021-06-04..2023-01-01: $+0.82 (-0.7% of total)
    - 2023-01-01..2024-07-30: $-219.65 (+178.1% of total)
    - 2024-07-30..2026-02-26: $-71.58 (+58.0% of total)
- Regime breakdown:
    - `RISK-ON`: $-13.35 over 82 trades ($-0.16/trade)
    - `NEUTRAL`: $-110.00 over 15 trades ($-7.33/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t4_r5_tight_cross` — **KILL**

- Trades: 65 over 62 days · total $-400.00 · avg $-6.1538/trade · win-rate 23.1%
- Sharpe: -4.05 · t-stat: -2.01 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-760.00, $+20.00] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-513.10 ✗
    - @ $-5.00/trade → $-725.00 ✗
    - @ $-10.00/trade → $-1050.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-02-20: $+20.00 (-5.0% of total)
    - 2020-02-20..2020-11-20: $-20.00 (+5.0% of total)
    - 2020-11-20..2021-08-21: $-60.00 (+15.0% of total)
    - 2021-08-21..2022-05-23: $-340.00 (+85.0% of total)
- Regime breakdown:
    - `UNKNOWN`: $-400.00 over 65 trades ($-6.15/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `n7_pct_adaptive_long` — **KILL**

- Trades: 16 over 16 days · total $-420.00 · avg $-26.2500/trade · win-rate 18.8%
- Sharpe: -5.74 · t-stat: -1.45 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-960.00, $+120.00] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-447.84 ✗
    - @ $-5.00/trade → $-500.00 ✗
    - @ $-10.00/trade → $-580.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-01-18: $-120.00 (+28.6% of total)
    - 2021-01-18..2022-09-17: $-120.00 (+28.6% of total)
    - 2022-09-17..2024-05-16: $+0.00 (-0.0% of total)
    - 2024-05-16..2026-01-13: $-180.00 (+42.9% of total)
- Regime breakdown:
    - `UNKNOWN`: $-420.00 over 16 trades ($-26.25/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `n5_pct_long_morning` — **KILL**

- Trades: 104 over 97 days · total $-460.00 · avg $-4.4231/trade · win-rate 26.0%
- Sharpe: -2.68 · t-stat: -1.66 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-940.00, $+80.00] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-640.96 ✗
    - @ $-5.00/trade → $-980.00 ✗
    - @ $-10.00/trade → $-1500.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-01-18: $-240.00 (+52.2% of total)
    - 2021-01-18..2022-09-17: $-180.00 (+39.1% of total)
    - 2022-09-17..2024-05-16: $+0.00 (-0.0% of total)
    - 2024-05-16..2026-01-13: $-40.00 (+8.7% of total)
- Regime breakdown:
    - `UNKNOWN`: $-460.00 over 104 trades ($-4.42/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t16_r5_long_only` — **KILL**

- Trades: 112 over 101 days · total $-585.00 · avg $-5.2232/trade · win-rate 25.0%
- Sharpe: -3.46 · t-stat: -2.19 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1100.00, $-35.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-779.88 ✗
    - @ $-5.00/trade → $-1145.00 ✗
    - @ $-10.00/trade → $-1705.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-120.00 (+20.5% of total)
    - 2020-04-17..2021-03-14: $-325.00 (+55.6% of total)
    - 2021-03-14..2022-02-08: $-140.00 (+23.9% of total)
    - 2022-02-08..2023-01-05: $+0.00 (-0.0% of total)
- Regime breakdown:
    - `UNKNOWN`: $-585.00 over 112 trades ($-5.22/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t7_r5_morning_only` — **KILL**

- Trades: 135 over 116 days · total $-600.00 · avg $-4.4444/trade · win-rate 25.9%
- Sharpe: -2.97 · t-stat: -2.01 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1200.00, $+0.00] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-834.90 ✗
    - @ $-5.00/trade → $-1275.00 ✗
    - @ $-10.00/trade → $-1950.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-02-03: $-60.00 (+10.0% of total)
    - 2020-02-03..2020-10-17: $-220.00 (+36.7% of total)
    - 2020-10-17..2021-07-01: $-120.00 (+20.0% of total)
    - 2021-07-01..2022-03-16: $-200.00 (+33.3% of total)
- Regime breakdown:
    - `UNKNOWN`: $-600.00 over 135 trades ($-4.44/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t8_r5_afternoon_only` — **KILL**

- Trades: 102 over 94 days · total $-805.00 · avg $-7.8922/trade · win-rate 20.6%
- Sharpe: -5.35 · t-stat: -3.27 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1260.00, $-325.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-982.48 ✗
    - @ $-5.00/trade → $-1315.00 ✗
    - @ $-10.00/trade → $-1825.00 ✗
- Quarterly decomposition:
    - 2019-09-12..2020-07-10: $-160.00 (+19.9% of total)
    - 2020-07-10..2021-05-09: $-245.00 (+30.4% of total)
    - 2021-05-09..2022-03-08: $-280.00 (+34.8% of total)
    - 2022-03-08..2023-01-05: $-120.00 (+14.9% of total)
- Regime breakdown:
    - `UNKNOWN`: $-805.00 over 102 trades ($-7.89/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t17_r5_short_only` — **KILL**

- Trades: 125 over 113 days · total $-820.00 · avg $-6.5600/trade · win-rate 22.4%
- Sharpe: -4.29 · t-stat: -2.87 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1360.00, $-280.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1037.50 ✗
    - @ $-5.00/trade → $-1445.00 ✗
    - @ $-10.00/trade → $-2070.00 ✗
- Quarterly decomposition:
    - 2019-07-23..2020-04-06: $-20.00 (+2.4% of total)
    - 2020-04-06..2020-12-21: $-140.00 (+17.1% of total)
    - 2020-12-21..2021-09-06: $-500.00 (+61.0% of total)
    - 2021-09-06..2022-05-23: $-160.00 (+19.5% of total)
- Regime breakdown:
    - `UNKNOWN`: $-820.00 over 125 trades ($-6.56/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t15_r5_pm_no_volcap` — **KILL**

- Trades: 133 over 119 days · total $-1005.00 · avg $-7.5564/trade · win-rate 21.1%
- Sharpe: -5.17 · t-stat: -3.55 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1545.00, $-440.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1236.42 ✗
    - @ $-5.00/trade → $-1670.00 ✗
    - @ $-10.00/trade → $-2335.00 ✗
- Quarterly decomposition:
    - 2019-06-19..2020-11-30: $-360.00 (+35.8% of total)
    - 2020-11-30..2022-05-15: $-585.00 (+58.2% of total)
    - 2022-05-15..2023-10-28: $-40.00 (+4.0% of total)
    - 2023-10-28..2025-04-11: $-20.00 (+2.0% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1005.00 over 133 trades ($-7.56/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `n2_pct_long_only` — **KILL**

- Trades: 186 over 153 days · total $-1045.00 · avg $-5.6183/trade · win-rate 24.2%
- Sharpe: -3.73 · t-stat: -2.90 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1705.00, $-360.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1368.64 ✗
    - @ $-5.00/trade → $-1975.00 ✗
    - @ $-10.00/trade → $-2905.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-01-18: $-440.00 (+42.1% of total)
    - 2021-01-18..2022-09-17: $-505.00 (+48.3% of total)
    - 2022-09-17..2024-05-16: $-20.00 (+1.9% of total)
    - 2024-05-16..2026-01-13: $-80.00 (+7.7% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1045.00 over 186 trades ($-5.62/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `n3_pct_morning_only` — **KILL**

- Trades: 206 over 160 days · total $-1120.00 · avg $-5.4369/trade · win-rate 24.3%
- Sharpe: -3.88 · t-stat: -3.09 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1840.00, $-400.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1478.44 ✗
    - @ $-5.00/trade → $-2150.00 ✗
    - @ $-10.00/trade → $-3180.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-01-18: $-540.00 (+48.2% of total)
    - 2021-01-18..2022-09-17: $-540.00 (+48.2% of total)
    - 2022-09-17..2024-05-16: $+0.00 (-0.0% of total)
    - 2024-05-16..2026-01-13: $-40.00 (+3.6% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1120.00 over 206 trades ($-5.44/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t0_r5_tight_stop` — **KILL**

- Trades: 237 over 188 days · total $-1121.00 · avg $-4.7300/trade · win-rate 23.6%
- Sharpe: -4.18 · t-stat: -3.61 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1697.07, $-497.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1533.38 ✗
    - @ $-5.00/trade → $-2306.00 ✗
    - @ $-10.00/trade → $-3491.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-208.00 (+18.6% of total)
    - 2020-04-17..2021-03-14: $-321.00 (+28.6% of total)
    - 2021-03-14..2022-02-08: $-496.00 (+44.2% of total)
    - 2022-02-08..2023-01-05: $-96.00 (+8.6% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1121.00 over 237 trades ($-4.73/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t13_r5_pm_loose_cross` — **KILL**

- Trades: 183 over 155 days · total $-1165.00 · avg $-6.3661/trade · win-rate 23.0%
- Sharpe: -4.35 · t-stat: -3.41 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1800.00, $-494.75] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1483.42 ✗
    - @ $-5.00/trade → $-2080.00 ✗
    - @ $-10.00/trade → $-2995.00 ✗
- Quarterly decomposition:
    - 2019-08-01..2020-06-09: $-320.00 (+27.5% of total)
    - 2020-06-09..2021-04-18: $-325.00 (+27.9% of total)
    - 2021-04-18..2022-02-25: $-380.00 (+32.6% of total)
    - 2022-02-25..2023-01-05: $-140.00 (+12.0% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1165.00 over 183 trades ($-6.37/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t12_r5_pm_wide` — **KILL**

- Trades: 136 over 115 days · total $-1185.00 · avg $-8.7132/trade · win-rate 19.1%
- Sharpe: -6.30 · t-stat: -4.26 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1700.00, $-620.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1421.64 ✗
    - @ $-5.00/trade → $-1865.00 ✗
    - @ $-10.00/trade → $-2545.00 ✗
- Quarterly decomposition:
    - 2019-09-12..2020-07-10: $-240.00 (+20.3% of total)
    - 2020-07-10..2021-05-09: $-405.00 (+34.2% of total)
    - 2021-05-09..2022-03-08: $-400.00 (+33.8% of total)
    - 2022-03-08..2023-01-05: $-140.00 (+11.8% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1185.00 over 136 trades ($-8.71/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t6_r5_strict_flow` — **KILL**

- Trades: 220 over 181 days · total $-1245.00 · avg $-5.6591/trade · win-rate 24.1%
- Sharpe: -3.96 · t-stat: -3.36 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-1965.00, $-465.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1627.80 ✗
    - @ $-5.00/trade → $-2345.00 ✗
    - @ $-10.00/trade → $-3445.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-120.00 (+9.6% of total)
    - 2020-04-17..2021-03-14: $-505.00 (+40.6% of total)
    - 2021-03-14..2022-02-08: $-520.00 (+41.8% of total)
    - 2022-02-08..2023-01-05: $-100.00 (+8.0% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1245.00 over 220 trades ($-5.66/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t2_r5_rr25` — **KILL**

- Trades: 237 over 188 days · total $-1345.00 · avg $-5.6751/trade · win-rate 20.7%
- Sharpe: -3.54 · t-stat: -3.05 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2150.00, $-470.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1757.38 ✗
    - @ $-5.00/trade → $-2530.00 ✗
    - @ $-10.00/trade → $-3715.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-70.00 (+5.2% of total)
    - 2020-04-17..2021-03-14: $-615.00 (+45.7% of total)
    - 2021-03-14..2022-02-08: $-600.00 (+44.6% of total)
    - 2022-02-08..2023-01-05: $-60.00 (+4.5% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1345.00 over 237 trades ($-5.68/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `v3_2_rmult_partial_pb` — **KILL**

- Trades: 25 over 25 days · total $-1395.27 · avg $-55.8108/trade · win-rate 20.0%
- Sharpe: -3.39 · t-stat: -1.07 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-4110.36, $+793.24] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-1438.77 ✗
    - @ $-5.00/trade → $-1520.27 ✗
    - @ $-10.00/trade → $-1645.27 ✗
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $+188.30 (-13.5% of total)
    - 2021-06-02..2022-12-29: $+69.43 (-5.0% of total)
    - 2022-12-29..2024-07-25: $-2097.55 (+150.3% of total)
    - 2024-07-25..2026-02-20: $+444.55 (-31.9% of total)
- Regime breakdown:
    - `NEUTRAL`: $+0.00 over 1 trades ($+0.00/trade)
    - `RISK-ON`: $-1395.27 over 24 trades ($-58.14/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t9_r5_short_hold` — **KILL**

- Trades: 237 over 188 days · total $-1404.50 · avg $-5.9262/trade · win-rate 23.6%
- Sharpe: -4.17 · t-stat: -3.60 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2160.00, $-644.50] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1816.88 ✗
    - @ $-5.00/trade → $-2589.50 ✗
    - @ $-10.00/trade → $-3774.50 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-140.00 (+10.0% of total)
    - 2020-04-17..2021-03-14: $-584.50 (+41.6% of total)
    - 2021-03-14..2022-02-08: $-620.00 (+44.1% of total)
    - 2022-02-08..2023-01-05: $-60.00 (+4.3% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1404.50 over 237 trades ($-5.93/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `r5_real_wide_target` — **KILL**

- Trades: 237 over 188 days · total $-1405.00 · avg $-5.9283/trade · win-rate 23.6%
- Sharpe: -4.16 · t-stat: -3.60 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2150.00, $-625.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1817.38 ✗
    - @ $-5.00/trade → $-2590.00 ✗
    - @ $-10.00/trade → $-3775.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-140.00 (+10.0% of total)
    - 2020-04-17..2021-03-14: $-585.00 (+41.6% of total)
    - 2021-03-14..2022-02-08: $-620.00 (+44.1% of total)
    - 2022-02-08..2023-01-05: $-60.00 (+4.3% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1405.00 over 237 trades ($-5.93/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t10_r5_long_hold` — **KILL**

- Trades: 237 over 188 days · total $-1405.00 · avg $-5.9283/trade · win-rate 23.6%
- Sharpe: -4.16 · t-stat: -3.60 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2160.00, $-625.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1817.38 ✗
    - @ $-5.00/trade → $-2590.00 ✗
    - @ $-10.00/trade → $-3775.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-140.00 (+10.0% of total)
    - 2020-04-17..2021-03-14: $-585.00 (+41.6% of total)
    - 2021-03-14..2022-02-08: $-620.00 (+44.1% of total)
    - 2022-02-08..2023-01-05: $-60.00 (+4.3% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1405.00 over 237 trades ($-5.93/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t11_r5_no_cooldown` — **KILL**

- Trades: 237 over 188 days · total $-1405.00 · avg $-5.9283/trade · win-rate 23.6%
- Sharpe: -4.16 · t-stat: -3.60 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2160.00, $-625.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1817.38 ✗
    - @ $-5.00/trade → $-2590.00 ✗
    - @ $-10.00/trade → $-3775.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-140.00 (+10.0% of total)
    - 2020-04-17..2021-03-14: $-585.00 (+41.6% of total)
    - 2021-03-14..2022-02-08: $-620.00 (+44.1% of total)
    - 2022-02-08..2023-01-05: $-60.00 (+4.3% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1405.00 over 237 trades ($-5.93/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t1_r5_wide_stop` — **KILL**

- Trades: 237 over 188 days · total $-1465.50 · avg $-6.1835/trade · win-rate 24.9%
- Sharpe: -3.40 · t-stat: -2.94 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2370.15, $-529.50] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1877.88 ✗
    - @ $-5.00/trade → $-2650.50 ✗
    - @ $-10.00/trade → $-3835.50 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-88.50 (+6.0% of total)
    - 2020-04-17..2021-03-14: $-633.00 (+43.2% of total)
    - 2021-03-14..2022-02-08: $-744.00 (+50.8% of total)
    - 2022-02-08..2023-01-05: $+0.00 (-0.0% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1465.50 over 237 trades ($-6.18/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `r4_real_orderflow` — **KILL**

- Trades: 237 over 188 days · total $-1540.00 · avg $-6.4979/trade · win-rate 27.0%
- Sharpe: -5.16 · t-stat: -4.45 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2190.00, $-890.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1952.38 ✗
    - @ $-5.00/trade → $-2725.00 ✗
    - @ $-10.00/trade → $-3910.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-210.00 (+13.6% of total)
    - 2020-04-17..2021-03-14: $-500.00 (+32.5% of total)
    - 2021-03-14..2022-02-08: $-700.00 (+45.5% of total)
    - 2022-02-08..2023-01-05: $-130.00 (+8.4% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1540.00 over 237 trades ($-6.50/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t3_r5_rr175` — **KILL**

- Trades: 237 over 188 days · total $-1570.00 · avg $-6.6245/trade · win-rate 24.5%
- Sharpe: -4.97 · t-stat: -4.29 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2250.00, $-855.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1982.38 ✗
    - @ $-5.00/trade → $-2755.00 ✗
    - @ $-10.00/trade → $-3940.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2020-04-17: $-175.00 (+11.1% of total)
    - 2020-04-17..2021-03-14: $-565.00 (+36.0% of total)
    - 2021-03-14..2022-02-08: $-735.00 (+46.8% of total)
    - 2022-02-08..2023-01-05: $-95.00 (+6.1% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1570.00 over 237 trades ($-6.62/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t14_r5_pm_loose_flow` — **KILL**

- Trades: 160 over 140 days · total $-1665.00 · avg $-10.4062/trade · win-rate 16.2%
- Sharpe: -7.70 · t-stat: -5.74 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2205.00, $-1115.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-1943.40 ✗
    - @ $-5.00/trade → $-2465.00 ✗
    - @ $-10.00/trade → $-3265.00 ✗
- Quarterly decomposition:
    - 2019-05-13..2020-04-10: $-720.00 (+43.2% of total)
    - 2020-04-10..2021-03-09: $-440.00 (+26.4% of total)
    - 2021-03-09..2022-02-05: $-345.00 (+20.7% of total)
    - 2022-02-05..2023-01-05: $-160.00 (+9.6% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1665.00 over 160 trades ($-10.41/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `r1_real_volfilter` — **KILL**

- Trades: 178 over 151 days · total $-1860.00 · avg $-10.4494/trade · win-rate 19.1%
- Sharpe: -8.36 · t-stat: -6.47 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-2360.00, $-1310.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-2169.72 ✗
    - @ $-5.00/trade → $-2750.00 ✗
    - @ $-10.00/trade → $-3640.00 ✗
- Quarterly decomposition:
    - 2019-05-13..2020-04-10: $-830.00 (+44.6% of total)
    - 2020-04-10..2021-03-09: $-440.00 (+23.7% of total)
    - 2021-03-09..2022-02-05: $-380.00 (+20.4% of total)
    - 2022-02-05..2023-01-05: $-210.00 (+11.3% of total)
- Regime breakdown:
    - `UNKNOWN`: $-1860.00 over 178 trades ($-10.45/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `v3_3_fib_noptl_pb` — **KILL**

- Trades: 25 over 25 days · total $-2305.28 · avg $-92.2112/trade · win-rate 20.0%
- Sharpe: -5.42 · t-stat: -1.71 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-5092.66, $+78.69] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-2348.78 ✗
    - @ $-5.00/trade → $-2430.28 ✗
    - @ $-10.00/trade → $-2555.28 ✗
- Quarterly decomposition:
    - 2019-11-06..2021-06-02: $-404.86 (+17.6% of total)
    - 2021-06-02..2022-12-29: $-366.89 (+15.9% of total)
    - 2022-12-29..2024-07-25: $-2236.29 (+97.0% of total)
    - 2024-07-25..2026-02-20: $+702.76 (-30.5% of total)
- Regime breakdown:
    - `NEUTRAL`: $-140.67 over 1 trades ($-140.67/trade)
    - `RISK-ON`: $-2164.61 over 24 trades ($-90.19/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `n0_pct_baseline` — **KILL**

- Trades: 368 over 247 days · total $-2525.00 · avg $-6.8614/trade · win-rate 22.0%
- Sharpe: -5.17 · t-stat: -5.11 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-3450.00, $-1565.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-3165.32 ✗
    - @ $-5.00/trade → $-4365.00 ✗
    - @ $-10.00/trade → $-6205.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-01-18: $-840.00 (+33.3% of total)
    - 2021-01-18..2022-09-17: $-1585.00 (+62.8% of total)
    - 2022-09-17..2024-05-16: $-20.00 (+0.8% of total)
    - 2024-05-16..2026-01-13: $-80.00 (+3.2% of total)
- Regime breakdown:
    - `UNKNOWN`: $-2525.00 over 368 trades ($-6.86/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `micro_fallback_pm30` — **KILL**

- Trades: 192 over 186 days · total $-2735.38 · avg $-14.2468/trade · win-rate 48.4%
- Sharpe: -0.90 · t-stat: -0.77 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-9674.46, $+3911.76] — INCLUDES ZERO
- Cost sensitivity:
    - @ $-1.74/trade → $-3069.46 ✗
    - @ $-5.00/trade → $-3695.38 ✗
    - @ $-10.00/trade → $-4655.38 ✗
- Quarterly decomposition:
    - 2019-05-17..2021-01-27: $+405.67 (-14.8% of total)
    - 2021-01-27..2022-10-10: $+547.43 (-20.0% of total)
    - 2022-10-10..2024-06-22: $-4766.24 (+174.2% of total)
    - 2024-06-22..2026-03-05: $+1077.76 (-39.4% of total)
- Regime breakdown:
    - `NEUTRAL`: $-303.30 over 21 trades ($-14.44/trade)
    - `RISK-ON`: $-2432.08 over 171 trades ($-14.22/trade)
- Findings:
    - total PnL <= 0
    - 95% bootstrap CI on total PnL covers zero
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `t5_r5_loose_flow` — **KILL**

- Trades: 348 over 263 days · total $-3145.00 · avg $-9.0374/trade · win-rate 18.4%
- Sharpe: -6.87 · t-stat: -7.02 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-3960.37, $-2295.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-3750.52 ✗
    - @ $-5.00/trade → $-4885.00 ✗
    - @ $-10.00/trade → $-6625.00 ✗
- Quarterly decomposition:
    - 2019-05-13..2020-04-10: $-1320.00 (+42.0% of total)
    - 2020-04-10..2021-03-09: $-1040.00 (+33.1% of total)
    - 2021-03-09..2022-02-05: $-725.00 (+23.1% of total)
    - 2022-02-05..2023-01-05: $-60.00 (+1.9% of total)
- Regime breakdown:
    - `UNKNOWN`: $-3145.00 over 348 trades ($-9.04/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `r2_real_trend_morn` — **KILL**

- Trades: 379 over 280 days · total $-3730.00 · avg $-9.8417/trade · win-rate 20.3%
- Sharpe: -8.57 · t-stat: -9.03 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-4480.00, $-2930.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-4389.46 ✗
    - @ $-5.00/trade → $-5625.00 ✗
    - @ $-10.00/trade → $-7520.00 ✗
- Quarterly decomposition:
    - 2019-05-13..2020-04-10: $-1560.00 (+41.8% of total)
    - 2020-04-10..2021-03-09: $-1080.00 (+29.0% of total)
    - 2021-03-09..2022-02-05: $-900.00 (+24.1% of total)
    - 2022-02-05..2023-01-05: $-190.00 (+5.1% of total)
- Regime breakdown:
    - `UNKNOWN`: $-3730.00 over 379 trades ($-9.84/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `r3_real_hard_pause` — **KILL**

- Trades: 379 over 280 days · total $-3730.00 · avg $-9.8417/trade · win-rate 20.3%
- Sharpe: -8.57 · t-stat: -9.03 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-4480.00, $-2930.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-4389.46 ✗
    - @ $-5.00/trade → $-5625.00 ✗
    - @ $-10.00/trade → $-7520.00 ✗
- Quarterly decomposition:
    - 2019-05-13..2020-04-10: $-1560.00 (+41.8% of total)
    - 2020-04-10..2021-03-09: $-1080.00 (+29.0% of total)
    - 2021-03-09..2022-02-05: $-900.00 (+24.1% of total)
    - 2022-02-05..2023-01-05: $-190.00 (+5.1% of total)
- Regime breakdown:
    - `UNKNOWN`: $-3730.00 over 379 trades ($-9.84/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `n1_pct_loose` — **KILL**

- Trades: 459 over 301 days · total $-3865.00 · avg $-8.4205/trade · win-rate 19.4%
- Sharpe: -6.52 · t-stat: -7.13 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-4850.00, $-2870.00] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-4663.66 ✗
    - @ $-5.00/trade → $-6160.00 ✗
    - @ $-10.00/trade → $-8455.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-01-18: $-980.00 (+25.4% of total)
    - 2021-01-18..2022-09-17: $-2425.00 (+62.7% of total)
    - 2022-09-17..2024-05-16: $-240.00 (+6.2% of total)
    - 2024-05-16..2026-01-13: $-220.00 (+5.7% of total)
- Regime breakdown:
    - `UNKNOWN`: $-3865.00 over 459 trades ($-8.42/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `r6_real_allday` — **KILL**

- Trades: 836 over 638 days · total $-5341.00 · avg $-6.3888/trade · win-rate 27.2%
- Sharpe: -5.01 · t-stat: -7.97 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-6567.50, $-4083.45] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-6795.64 ✗
    - @ $-5.00/trade → $-9521.00 ✗
    - @ $-10.00/trade → $-13701.00 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-02-08: $-1907.50 (+35.7% of total)
    - 2021-02-08..2022-10-30: $-1763.50 (+33.0% of total)
    - 2022-10-30..2024-07-20: $-890.00 (+16.7% of total)
    - 2024-07-20..2026-04-10: $-780.00 (+14.6% of total)
- Regime breakdown:
    - `UNKNOWN`: $-5341.00 over 836 trades ($-6.39/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `n4_pct_no_vol` — **KILL**

- Trades: 8101 over 1145 days · total $-131561.50 · avg $-16.2402/trade · win-rate 6.3%
- Sharpe: -20.86 · t-stat: -44.47 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-134118.08, $-129013.91] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-145657.24 ✗
    - @ $-5.00/trade → $-172066.50 ✗
    - @ $-10.00/trade → $-212571.50 ✗
- Quarterly decomposition:
    - 2019-05-22..2021-02-09: $-1180.00 (+0.9% of total)
    - 2021-02-09..2022-11-01: $-10665.00 (+8.1% of total)
    - 2022-11-01..2024-07-23: $-62978.00 (+47.9% of total)
    - 2024-07-23..2026-04-14: $-56738.50 (+43.1% of total)
- Regime breakdown:
    - `UNKNOWN`: $-131561.50 over 8101 trades ($-16.24/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

### `r0_real_baseline` — **KILL**

- Trades: 12695 over 1100 days · total $-213152.50 · avg $-16.7903/trade · win-rate 6.4%
- Sharpe: -25.27 · t-stat: -52.80 · DSR@30=0.00 · DSR@100=0.00
- Bootstrap 95% CI on total PnL: [$-215793.52, $-210488.40] — excludes zero
- Cost sensitivity:
    - @ $-1.74/trade → $-235241.80 ✗
    - @ $-5.00/trade → $-276627.50 ✗
    - @ $-10.00/trade → $-340102.50 ✗
- Quarterly decomposition:
    - 2019-05-13..2021-02-03: $-1600.00 (+0.8% of total)
    - 2021-02-03..2022-10-28: $-15810.00 (+7.4% of total)
    - 2022-10-28..2024-07-21: $-99348.00 (+46.6% of total)
    - 2024-07-21..2026-04-14: $-96394.50 (+45.2% of total)
- Regime breakdown:
    - `UNKNOWN`: $-213152.50 over 12695 trades ($-16.79/trade)
- Findings:
    - total PnL <= 0
    - DSR@100 trials = 0.00 — edge indistinguishable from multi-testing noise
    - PnL goes negative at $-5/trade friction

## Aggregate verdict

- **PASS**: 6 (10.5%)
- **WATCH**: 0 (0.0%)
- **FRAGILE**: 0 (0.0%)
- **FAIL**: 0 (0.0%)
- **KILL**: 51 (89.5%)

_Decomposition ran across 57 variants. Verdict bar is intentionally harsh — a `PASS` here is a variant ready for paper soak, not just a pretty backtest._

