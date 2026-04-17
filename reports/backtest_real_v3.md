# Batch 13 — V3 Real-Tape Backtest — 2026-04-16 22:54 UTC

Apex V3 15-voice engine on Databento MNQ 1m tape, aggregated to 5m for detection.
**1652 clean RTH days** (2019-05-06 → 2026-04-14)

Zero slippage, zero commission. Exits resolved on 1m bars for tick-precision.
Intermarket voices (V8-V11) return 0 — no sibling data in tape.

## Variant Summary

| Variant | Trades | Signals | W | L | WR% | Total R | Avg R | PF | MaxDD R | $ PnL | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v3_4_fib_partial_pm30 | 192 | 192 | 100 | 29 | 52.1 | +20.19 | +0.105 | 1.70 | 10.15 | $+5,527.56 | +1.41 |
| v3_0_fib_partial_pb | 25 | 25 | 19 | 3 | 76.0 | +7.03 | +0.281 | 3.34 | 2.50 | $+3,432.28 | +5.49 |
| v3_1_fib_partial_mkt | 25 | 25 | 19 | 3 | 76.0 | +7.03 | +0.281 | 3.34 | 2.50 | $+3,432.28 | +5.49 |
| v3_6_firm_only | 25 | 25 | 19 | 3 | 76.0 | +7.03 | +0.281 | 3.34 | 2.50 | $+3,432.28 | +5.49 |
| v3_7_fib_full_session | 25 | 25 | 19 | 3 | 76.0 | +7.03 | +0.281 | 3.34 | 2.50 | $+3,432.28 | +5.49 |
| v3_8_hybrid_exits | 25 | 25 | 19 | 3 | 76.0 | +7.03 | +0.281 | 3.34 | 2.50 | $+3,432.28 | +5.49 |
| v3_9_strong_redteam | 25 | 25 | 19 | 3 | 76.0 | +7.03 | +0.281 | 3.34 | 2.50 | $+3,432.28 | +5.49 |
| v3_5_fib_partial_pm50 | 2 | 2 | 1 | 0 | 50.0 | +0.50 | +0.250 | ∞ | 0.00 | $+144.19 | +11.22 |
| v3_2_rmult_partial_pb | 25 | 25 | 5 | 3 | 20.0 | -1.30 | -0.052 | 0.57 | 3.00 | $-1,395.25 | -3.39 |
| v3_3_fib_noptl_pb | 25 | 25 | 5 | 17 | 20.0 | -4.32 | -0.173 | 0.20 | 5.27 | $-2,305.26 | -5.42 |

### v3_4_fib_partial_pm30

- **Trades:** 192 (of 192 signals fired)
- **Days traded:** 186 / 1652
- **Avg MFE:** +0.26R  |  Avg MAE: -0.57R

  **By Setup:**
  - EMA PB: 83 trades, 20% WR, -9.70R
  - ORB: 109 trades, 76% WR, +29.89R

  **By Regime:**
  - NEUTRAL: 21 trades, 57% WR, +3.80R
  - RISK-ON: 171 trades, 51% WR, +16.39R

### v3_0_fib_partial_pb

- **Trades:** 25 (of 25 signals fired)
- **Days traded:** 25 / 1652
- **Avg MFE:** +0.14R  |  Avg MAE: -0.44R

  **By Setup:**
  - ORB: 25 trades, 76% WR, +7.03R

  **By Regime:**
  - NEUTRAL: 1 trades, 100% WR, +0.50R
  - RISK-ON: 24 trades, 75% WR, +6.53R

### v3_1_fib_partial_mkt

- **Trades:** 25 (of 25 signals fired)
- **Days traded:** 25 / 1652
- **Avg MFE:** +0.14R  |  Avg MAE: -0.44R

  **By Setup:**
  - ORB: 25 trades, 76% WR, +7.03R

  **By Regime:**
  - NEUTRAL: 1 trades, 100% WR, +0.50R
  - RISK-ON: 24 trades, 75% WR, +6.53R

## Verdict

**V3 ENGINE HAS EDGE** — Best: v3_4_fib_partial_pm30 with 192 trades, 52% WR, +20.19R total, $+5,527.56 over 1652 days.

*Generated in 118.3s*