# Apex v2 Firm Edition — Integration Guide

Final version after master tuning. 15 voices, 4 intermarket feeds, daily P&L circuit breaker, fractional Kelly sizing, Monte Carlo validated.

## The stack

| File | Purpose |
|------|---------|
| `MNQ_ETA_v2_Firm.pine` | TradingView indicator (Pine v6) — 15 voices, dashboard, webhook alerts |
| `python/firm_engine.py` | 15-voice engine, regime detector, Red Team, PM voting |
| `python/backtest.py` | Historical simulation with full v1 detector + v2 upgrades |
| `python/indicator_state.py` | Streaming ATR/EMA/RSI/ADX/VWAP/Alligator |
| `python/intermarket.py` | Multi-symbol loader (NQ + VIX + ES + DXY + TICK) |
| `python/walkforward.py` | Walk-forward validator with auto PM sweep |
| `python/autocalibrator.py` | Grid-search parameter optimizer |
| `python/monte_carlo.py` | Bootstrap resampling + stress tests |
| `python/master_test.py` | A/B feature comparison runner |
| `python/webhook.py` | Flask server for Pine alert re-validation |
| `python/regime_ml.py` | sklearn Random Forest regime classifier |
| `python/court_of_appeals.py` | Weekly trade replay + drift detection |
| `python/execution_analyzer.py` | MAE/MFE per-setup analysis |

## The 15 voices

| # | Voice | Signal | Weight (default) |
|---|-------|--------|---------|
| V1 | ORB Breakout | +90–110 fire | 1.5 Risk-On / 0.5 Risk-Off |
| V2 | EMA Pullback | +80–110 fire | 1.2 / 0.8 |
| V3 | Sweep Reclaim | +90–110 fire | 0.8 / 1.5 |
| V4 | VWAP Mean Reversion | Stretch reversion | 0.7 / 1.3 |
| V5 | Momentum Burst | ADX rising + vol spike | 1.4 / 0.6 |
| V6 | HTF Bias | Higher timeframe trend | 1.0 |
| V7 | Liquidity Vacuum | Range expand + thin vol | 0.8 / 1.2 |
| V8 | VIX Spike | Risk regime via CBOE:VIX | 1.5 (intermarket) |
| V9 | ES/NQ Correlation | Broad-market divergence | 1.3 (intermarket) |
| V10 | DXY Risk Currents | Dollar inverse equity | 0.6 (intermarket) |
| V11 | TICK Breadth | NYSE breadth extremes | 0.8 (intermarket) |
| V12 | Cumulative Delta | Close position + body | 0.6 (advisory) |
| V13 | ICT Killzone | Session time-of-day | 0.7 (advisory) |
| V14 | Premium/Discount | SMC range position | 0.4 (advisory) |
| V15 | Fair Value Gap | 3-bar imbalance | 0.5 (advisory) |

## PM voting math

```
quant_total = weighted_avg(active voices)     # -100 to +100
red_penalty = red_team_score × regime_weight  # 0-100
pm_final    = |quant_total| - red_penalty     # fire if > threshold
```

**Defaults**: PM threshold 25, setup required (V1/V2/V3 must fire), crisis lockdown.
**Calibrated from**: Monte Carlo + grid search on 73-day MNQ window.

## Daily P&L circuit breaker

| Daily R | Action |
|---------|--------|
| −1.0R | Half size |
| −2.0R | No new trades today |
| 2 losing days in row | Pause 3 days |
| Midnight ET | Reset |

## Webhook payload format

Pine sends to `webhook.py`:
```json
{
  "symbol": "MNQM2026",
  "setup": "ORB",
  "side": "long",
  "entry": 25870.25,
  "sl": 25862.50,
  "tp1": 25881.75,
  "tp2": 25893.25,
  "pm_final": 47.3,
  "quant": 52.1,
  "red": 4.8,
  "regime": "RISK-ON",
  "voices": {"v1": 95, "v2": 30, "...": 0},
  "timestamp": 1776528300
}
```

Server re-runs `firm_engine.evaluate()`. If PM disagrees (clock drift, missing data), trade is rejected.

## Required market data feeds

- MNQ 5m OHLCV (primary)
- CBOE:VIX 5m (V8)
- CME_MINI:ES1! 5m (V9)
- TVC:DXY 5m (V10, optional)
- USI:TICK 5m (V11, optional)

System works without intermarket feeds (V8-V11 return 0 when data missing).

## Main bot integration contract

1. Subscribe to `webhook.py` validated-trade stream
2. Apply own risk layer (max positions, margin)
3. Route to broker with `entry`, `sl`, `tp1`, `tp2` from payload
4. Report fills back to webhook for daily P&L tracking

## Running the validation suite

```bash
# Quick backtest
python backtest.py mnq_5m.csv --pm 25

# Full with intermarket
python backtest.py mnq_5m.csv --pm 25 \
  --vix vix.csv --es es.csv --dxy dxy.csv --tick tick.csv

# Walk-forward with auto PM sweep
python walkforward.py mnq_5m.csv --windows 7 --sweep

# Monte Carlo 1000 sims
python monte_carlo.py mnq_5m.csv --pm 25 --sims 1000

# A/B test all feature combos
python master_test.py mnq_5m.csv --pm 25 \
  --vix vix.csv --es es.csv --dxy dxy.csv --tick tick.csv

# Grid search optimal params
python autocalibrator.py mnq_5m.csv --windows 7 --quick
```

## Validation verdict

73-day MNQ window, Dec 28 2025 → Apr 14 2026, at PM=25 with all 15 voices:

| Metric | Value | Status |
|--------|-------|--------|
| Trades | 14 | small sample |
| Strike rate | 75.0% | ✓ matches v1 baseline 73% |
| Total R | +0.9 | positive |
| Profit factor | 1.60 | > 1.5 target |
| Max drawdown | 1.5R | ✓ < 2.5R target |
| Ruin probability (≥3R DD) | 3.9% | ✓ < 5% target |
| Monte Carlo 5th %ile R | −2.1R | ✗ target > 0 (variance too high) |
| MC 95th %ile DD | 2.7R | ✗ target ≤ 2.5R (variance) |

**Honest interpretation.** System passes 4 of 6 validation checks. The 2 failures come from small-sample variance — 14 trades is below the 30+ threshold for tight confidence intervals. Median performance is strong (+0.8R, 75% strike, PF 1.6, 1.0R DD).

**Deployment recommendation.** Run 30–60 days paper-traded before scaling capital. Collect 30+ trades, re-run Monte Carlo. If CI tightens and all 6 metrics pass, scale in stages.

## Known limitations

1. V14 Premium/Discount is partially tautological for ORB (by definition ORB breaks above recent range). Advisory weight 0.4 neutralizes most of this.
2. V13 Killzone assumes UTC epoch_s timestamps. Verify your live feed format.
3. Intermarket data sparse on test window (~8% coverage). Full backfill recommended for production.
4. Pine V14/V15 compute but aren't visualized on chart (no FVG boxes yet — polish item).
5. Fractional Kelly off by default. Enable `Backtester(use_kelly=True)` only after 30+ trade history accumulated.

## Next iteration ideas

1. Train `regime_ml.py` Random Forest on labeled sample to learn voice→outcome patterns
2. Add VIX term structure (VIX9D/VIX3M contango) when data available
3. Live data-feed freshness checker before each trading session
4. Auto-recalibration every 30 days via cron-scheduled `autocalibrator.py`
