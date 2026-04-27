# Firm Review — `r5_real_wide_target`

_Auto-generated 2026-04-26 from `scripts/firm_review.py` · data: real_mnq_1m_rth (1 days)_

## Configuration

```python
rr = 2.0
risk_ticks = 40
time_stop_bars = 20
cross_magnitude_min = 2.0
vol_filter_stdev_max = 17.0
vol_hard_pause_stdev = 28.0
trend_align_bars = 5
orderflow_proxy_min = 0.6
morning_window = (30, 120)
afternoon_window = (270, 375)
loss_cooldown_bars = 3
```

## Stage 1 — Quant (Spec)

- **Sample size:** 1 trades over 1 days
- **Net PnL:** $-21.00
- **Expectancy / trade:** $-21.00 (= -1.050 R)
- **Win rate:** 0.0%
- **95% bootstrap CI on total PnL:** $-21.00 / $-21.00
- **Risk per trade (spec):** 40 ticks = $20.00

### Per-regime breakdown

| Regime | n | wins | win% | net PnL |
|---|---:|---:|---:|---:|
| `real_trend_down` | 1 | 0 | 0.0% | $-21.00 |

### Per exit reason

| Reason | n | net PnL |
|---|---:|---:|
| `stop` | 1 | $-21.00 |

## Stage 2 — Red Team (Attack)

- **Sample size.** n=1 trades over 1 days is well below the 30-trade threshold for estimating expectancy. The bootstrap CI straddles zero — we cannot reject a null of zero edge.
- **Regime bleed.** `real_trend_down` contributes $-21.00 across 1 trades. If this regime dominates the next month, net PnL turns negative.
- **Slippage drag.** Live-sim journal shows +1.11 ticks mean slippage (p95 +2.0). At 40-tick stops this is a material cost drag.

## Stage 3 — Risk Manager (Sizing)

- **Full Kelly estimate:** 0.000 (uses observed WR and spec rr)
- **Fractional Kelly (1/4, capped 2%):** 0.00% of equity per trade
- **Risk per trade in dollars:** $20.00 per contract, 1 contract
- **Daily stop:** -3R (hard breaker at -$60 on a 40-tick risk)
- **Weekly stop:** -8R
- **Drawdown kill:** -15R peak-to-trough
- **Comment:** sample is too small for sizing above 1 contract. Kelly is directional only; position size is dictated by the risk budget, not the calculation above, until n>50 trades.

## Stage 4 — Macro (Regime)

- **Instrument:** MNQ (micro E-mini Nasdaq-100)
- **Session filter:** (30, 120) AM / (270, 375) PM (bar index, 1m)
- **Volatility gate:** stdev_max=17.0, hard_pause=28.0
- **Competence:** per-regime table above is the competence matrix. Do NOT trade this variant when realized 1m stdev exceeds the hard-pause level — it is un-tested territory for this config.

## Stage 5 — Micro (Execution)

- **Journal trades:** 37
- **Mean slippage:** +1.11 ticks
- **p95 slippage:** +2.00 ticks
- **Journal net PnL:** $+101.62
- **Fill assumption:** limit entries with 1-tick simulated slippage, market exits on stop/target. Production must match this assumption on the broker side or expectancy shifts.

## Stage 6 — PM (Decide)

- **Verdict:** **HOLD.** Sample or expectancy does not clear the ship threshold (n≥8, E[trade]>0, CI upper>0).
- **Monitoring:** every 10 new trades, rerun this review and diff against prior memo

## One-page Decision Memo

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY DECISION MEMO
ID: r5_real_wide_target   Date: 2026-04-26   Author: edward avila
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

THESIS (one sentence)
  EMA9/EMA21 cross on MNQ 1m RTH with vol + flow gates, rr=2.0, risk=40t, captures afternoon drift more than morning noise.

EVIDENCE (3 bullets, numeric)
  • 1 trades / 1 days, net $-21.00, WR 0.0%, E[trade] $-21.00
  • 95% boot CI on total PnL: $-21.00 / $-21.00
  • Best regime bucket: `real_trend_down` (1 trades, $-21.00)

RED TEAM'S PRIMARY DISSENT (verbatim)
  **Sample size.** n=1 trades over 1 days is well below the 30-trade threshold for estimating expectancy. The bootstrap CI straddles zero — we cannot reject a null of zero edge.

RESOLUTION
  [ ] Fixed — how: _______
  [x] Accepted as surviving risk — monitoring: rerun memo every 10 trades
  [ ] Overridden — rationale: _______

SIZING
  Risk per trade: 0.00%   Kelly fraction: 0.000 (1/4 capped)
  Daily stop: -3R   Weekly: -8R   DD kill: -15R

FALSIFICATION
  I abandon this by 2026-05-26 if ANY of:
  • Live expectancy < +0.05R across first 30 new trades
  • Slippage p95 exceeds +3.0 ticks over any 10-trade window
  • Net PnL < lower-CI bound ($-21.00) for trailing 15 days
  • Any single loss exceeds 120 ticks (= 3x intended risk)

MONITORING
  First review: after 10 trades
  Success: E[trade] ≥ +0.10R, DD ≤ -5R
  Failure:  E[trade] ≤ 0, OR DD ≥ -15R

SIGNATURE: __________
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```