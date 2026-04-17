# Firm Review — `r5_real_wide_target`

_Auto-generated 2026-04-16 from `scripts/firm_review.py` · data: real_mnq_1m_rth (15 days)_

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

- **Sample size:** 8 trades over 15 days
- **Net PnL:** $+11.50
- **Expectancy / trade:** $+1.44 (= +0.072 R)
- **Win rate:** 37.5%
- **95% bootstrap CI on total PnL:** $-108.00 / $+136.50
- **Risk per trade (spec):** 40 ticks = $20.00

### Per-regime breakdown

| Regime | n | wins | win% | net PnL |
|---|---:|---:|---:|---:|
| `real_chop` | 0 | 0 | 0.0% | $+0.00 |
| `real_high_vol` | 3 | 1 | 33.3% | $-4.00 |
| `real_trend_down` | 3 | 1 | 33.3% | $-4.00 |
| `real_trend_up` | 2 | 1 | 50.0% | $+19.50 |

### Per exit reason

| Reason | n | net PnL |
|---|---:|---:|
| `stop` | 5 | $-108.00 |
| `take_profit` | 3 | $+119.50 |

## Stage 2 — Red Team (Attack)

- **Sample size.** n=8 trades over 15 days is well below the 30-trade threshold for estimating expectancy. The bootstrap CI straddles zero — we cannot reject a null of zero edge.
- **CI includes zero.** 95% bootstrap on total PnL is [$-108.00, $+136.50]. The lower bound shows a plausible net loss of this magnitude over the same 15-day window.
- **Win rate is low (37.5%).** Expectancy depends on 1-2 fat-tail winners. If the target-fill distribution changes (e.g. more choppy days), the strategy goes negative fast.
- **Regime bleed.** `real_trend_down` contributes $-4.00 across 3 trades. If this regime dominates the next month, net PnL turns negative.
- **Slippage drag.** Live-sim journal shows +1.11 ticks mean slippage (p95 +2.0). At 40-tick stops this is a material cost drag.

## Stage 3 — Risk Manager (Sizing)

- **Full Kelly estimate:** 0.062 (uses observed WR and spec rr)
- **Fractional Kelly (1/4, capped 2%):** 1.56% of equity per trade
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

- **Verdict:** **SHIP TO INTERNAL-SIM** with mandatory 30-day observation. Do not escalate to paper or live until falsification window completes clean.
- **Monitoring:** every 10 new trades, rerun this review and diff against prior memo

## One-page Decision Memo

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY DECISION MEMO
ID: r5_real_wide_target   Date: 2026-04-16   Author: edward avila
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

THESIS (one sentence)
  EMA9/EMA21 cross on MNQ 1m RTH with vol + flow gates, rr=2.0, risk=40t, captures afternoon drift more than morning noise.

EVIDENCE (3 bullets, numeric)
  • 8 trades / 15 days, net $+11.50, WR 37.5%, E[trade] $+1.44
  • 95% boot CI on total PnL: $-108.00 / $+136.50
  • Best regime bucket: `real_trend_up` (2 trades, $+19.50)

RED TEAM'S PRIMARY DISSENT (verbatim)
  **Sample size.** n=8 trades over 15 days is well below the 30-trade threshold for estimating expectancy. The bootstrap CI straddles zero — we cannot reject a null of zero edge.

RESOLUTION
  [ ] Fixed — how: _______
  [x] Accepted as surviving risk — monitoring: rerun memo every 10 trades
  [ ] Overridden — rationale: _______

SIZING
  Risk per trade: 1.56%   Kelly fraction: 0.016 (1/4 capped)
  Daily stop: -3R   Weekly: -8R   DD kill: -15R

FALSIFICATION
  I abandon this by 2026-05-16 if ANY of:
  • Live expectancy < +0.05R across first 30 new trades
  • Slippage p95 exceeds +3.0 ticks over any 10-trade window
  • Net PnL < lower-CI bound ($-108.00) for trailing 15 days
  • Any single loss exceeds 120 ticks (= 3x intended risk)

MONITORING
  First review: after 10 trades
  Success: E[trade] ≥ +0.10R, DD ≤ -5R
  Failure:  E[trade] ≤ 0, OR DD ≥ -15R

SIGNATURE: __________
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```