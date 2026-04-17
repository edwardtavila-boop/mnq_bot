# Batch 15 — Ensemble Combiner — 2026-04-16 23:02 UTC

Ensemble signal filters on V3 engine, Databento MNQ tape.
**1652 clean RTH days** (2019-05-06 → 2026-04-14)

## Variant Summary

| Variant | Trades | Signals | Blocked | W | L | WR% | Total R | Avg R | PF | MaxDD R | $ PnL | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_only_pm30 | 112 | 195 | 83 | 86 | 13 | 76.8 | +31.39 | +0.280 | 3.41 | 5.00 | $+11,215.09 | +4.31 |
| orb_regime_pm30 | 112 | 195 | 83 | 86 | 13 | 76.8 | +31.39 | +0.280 | 3.41 | 5.00 | $+11,215.09 | +4.31 |
| orb_sweep_pm30 | 112 | 195 | 83 | 86 | 13 | 76.8 | +31.39 | +0.280 | 3.41 | 5.00 | $+11,215.09 | +4.31 |
| orb_regime_conf_pm25 | 173 | 606 | 433 | 126 | 19 | 72.8 | +27.41 | +0.158 | 3.33 | 2.75 | $+10,594.28 | +4.38 |
| all_setups_pm30 | 192 | 192 | 0 | 100 | 29 | 52.1 | +20.19 | +0.105 | 1.70 | 10.15 | $+5,527.56 | +1.41 |
| orb_confidence_pm30 | 112 | 195 | 83 | 86 | 13 | 76.8 | +19.95 | +0.178 | 3.28 | 3.25 | $+7,125.52 | +4.11 |
| orb_regime_conf_pm30 | 112 | 195 | 83 | 86 | 13 | 76.8 | +19.95 | +0.178 | 3.28 | 3.25 | $+7,125.52 | +4.11 |
| orb_only_pm40 | 25 | 25 | 0 | 19 | 3 | 76.0 | +7.03 | +0.281 | 3.34 | 2.50 | $+3,432.28 | +5.49 |

### orb_only_pm30

- **Trades:** 112 / 195 signals (83 blocked by setup, 0 by regime)
- **Days traded:** 112 / 1652
- **Avg MFE:** +0.16R  |  Avg MAE: -0.46R

  **By Setup:**
  - ORB: 112 trades, 77% WR, +31.39R

  **By Regime:**
  - NEUTRAL: 13 trades, 92% WR, +6.00R
  - RISK-ON: 99 trades, 75% WR, +25.39R

### orb_regime_pm30

- **Trades:** 112 / 195 signals (83 blocked by setup, 0 by regime)
- **Days traded:** 112 / 1652
- **Avg MFE:** +0.16R  |  Avg MAE: -0.46R

  **By Setup:**
  - ORB: 112 trades, 77% WR, +31.39R

  **By Regime:**
  - NEUTRAL: 13 trades, 92% WR, +6.00R
  - RISK-ON: 99 trades, 75% WR, +25.39R

### orb_sweep_pm30

- **Trades:** 112 / 195 signals (83 blocked by setup, 0 by regime)
- **Days traded:** 112 / 1652
- **Avg MFE:** +0.16R  |  Avg MAE: -0.46R

  **By Setup:**
  - ORB: 112 trades, 77% WR, +31.39R

  **By Regime:**
  - NEUTRAL: 13 trades, 92% WR, +6.00R
  - RISK-ON: 99 trades, 75% WR, +25.39R

## Verdict

**BEST ENSEMBLE: orb_only_pm30**
- 112 trades over 1652 days (112 active)
- 77% WR, +31.39R total, PF 3.41
- Max DD: 5.00R, $+11,215.09

**vs. all_setups control:** +11.20R lift (112 vs 192 trades)

## Key Findings

1. **ORB is the only setup with edge** — EMA PB is consistently negative
2. **Fibonacci exits + partials = essential** — R-multiple exits destroy edge
3. **PM threshold trades quality vs quantity** — PM30 gets 109 ORB trades at 76% WR
4. **Micro entry refinement doesn't help** — tighter stops get whipsawed

*Generated in 95.0s*