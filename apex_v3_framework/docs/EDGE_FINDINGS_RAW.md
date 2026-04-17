# Edge Discovery Findings — V1 LOCKED at PM=25.0

Generated: 2026-04-15T23:31:51.318651

## Sample
- Total trades: 193
- Total R: -2.65
- Avg R/trade: -0.0137

## By Setup

| Setup | n | Win% | Strike% | Avg R | Total R | PF | Avg MFE | Avg MAE |
|-------|---|------|---------|-------|---------|-----|---------|---------|
| EMA PB | 29 | 72.4 | 84.0 | +0.040 | +1.15 | 1.57 | 0.83 | -0.62 |
| SWEEP | 5 | 60.0 | 75.0 | -0.020 | -0.10 | 0.9 | 0.58 | -0.51 |
| ORB | 159 | 39.6 | 67.0 | -0.023 | -3.70 | 0.88 | 0.55 | -0.51 |

## By Time of Day (RTH only, excluding pre/after market)

| TOD | n | Win% | Strike% | Avg R | Total R | PF |
|-----|---|------|---------|-------|---------|-----|
| lunch | 14 | 28.6 | 100.0 | +0.171 | +2.40 | inf |
| mid_am | 20 | 50.0 | 83.3 | +0.107 | +2.15 | 3.15 |
| moc | 12 | 41.7 | 100.0 | +0.100 | +1.20 | inf |
| power_hour | 12 | 41.7 | 83.3 | +0.004 | +0.05 | 1.05 |
| early_pm | 9 | 22.2 | 66.7 | -0.044 | -0.40 | 0.6 |
| open_30min | 126 | 48.4 | 65.6 | -0.064 | -8.05 | 0.74 |

## By Regime

| Regime | n | Win% | Strike% | Avg R | Total R | PF |
|--------|---|------|---------|-------|---------|-----|
| RISK-ON | 122 | 45.9 | 72.7 | +0.018 | +2.25 | 1.12 |
| NEUTRAL | 71 | 43.7 | 67.4 | -0.069 | -4.90 | 0.66 |

## TOP 15 Setup × Time-of-Day Combinations (positive expectancy, n>=5)

| Bucket | n | Win% | Strike% | Avg R | Total R | PF |
|--------|---|------|---------|-------|---------|-----|
| ORB_mid_am | 8 | 37.5 | 100.0 | +0.262 | +2.10 | inf |
| ORB_lunch | 13 | 23.1 | 100.0 | +0.162 | +2.10 | inf |
| ORB_moc | 10 | 30.0 | 100.0 | +0.090 | +0.90 | inf |
| ORB_early_pm | 7 | 14.3 | 100.0 | +0.043 | +0.30 | inf |
| EMA PB_open_30min | 11 | 81.8 | 81.8 | +0.032 | +0.35 | 1.35 |
| EMA PB_mid_am | 12 | 58.3 | 77.8 | +0.004 | +0.05 | 1.05 |

## BOTTOM 10 Setup × TOD (negative expectancy — avoid these)

| Bucket | n | Win% | Strike% | Avg R | Total R | PF |
|--------|---|------|---------|-------|---------|-----|
| ORB_power_hour | 7 | 14.3 | 50.0 | -0.100 | -0.70 | 0.3 |
| ORB_open_30min | 114 | 45.6 | 63.4 | -0.074 | -8.40 | 0.72 |

## TOP 10 Voice Signatures (pattern of voices firing together)

| Voice signature | n | Win% | Strike% | Avg R | Total R |
|-----------------|---|------|---------|-------|---------|
| v2-|v6- | 5 | 80.0 | 100.0 | +0.120 | +0.60 |
| v1-|v12-|v15-|v5-|v6- | 18 | 27.8 | 71.4 | +0.106 | +1.90 |
| v1+|v12+|v15+|v5+|v6+ | 18 | 38.9 | 77.8 | +0.072 | +1.30 |
| v1+|v12+|v6+ | 6 | 66.7 | 80.0 | +0.033 | +0.20 |
| v2+|v6+ | 21 | 71.4 | 78.9 | +0.012 | +0.25 |
| v1+|v12+|v15+|v5+ | 5 | 60.0 | 75.0 | -0.020 | -0.10 |
| v1-|v12-|v5-|v6- | 30 | 43.3 | 65.0 | -0.063 | -1.90 |
| v1+|v6+ | 9 | 44.4 | 57.1 | -0.067 | -0.60 |
| v1-|v6- | 10 | 40.0 | 66.7 | -0.080 | -0.80 |
| v1+|v12+|v5+|v6+ | 36 | 41.7 | 60.0 | -0.119 | -4.30 |

## Expiration Deep Dive

- Total expired trades: 70 (36.3% of all trades)
- Avg MFE while open: 0.392R
- Avg MAE while open: -0.457R
- Expired trades that hit MFE >= 0.5R: 22
- Expired trades that hit MFE >= 1.0R: 5
- **Potential R recovered with 0.5R partial-take rule**: +11.0R
- **Potential R recovered with 1.0R partial-take rule**: +5.0R

## What this tells us

- Best-performing setup: **EMA PB** at +0.040R/trade (29 trades)
- Best time-of-day: **lunch** at +0.171R/trade (14 trades)
- Best setup × TOD combination with statistical weight: **ORB_lunch** at +0.162R/trade (13 trades, 100.0% strike)

- **Time-stop opportunity**: A 0.5R partial-take rule on stalled trades would recover +11.0R that V1 currently leaves on the table

## Next Step

Take the TOP buckets above (positive expectancy + n>=10) and use them as the V2 spec.
Implement them as orthogonal filters (require setup ∈ best_setups AND time ∈ best_TODs AND regime ∈ best_regimes).
That replaces the PM gate with data-derived confluence.