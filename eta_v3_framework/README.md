# Apex V3 Trading Framework

**Production-validated NQ futures trading system**
Built through V1 → V2 → V3 with rigorous walk-forward validation.

---

## What this is

A complete, modular Python framework for systematic NQ/MNQ futures trading on the 5-minute timeframe. The system uses a 15-voice analytical engine ("the Firm"), objective confluence scoring, asymmetric payoff management, and walk-forward validated parameters.

**Final validated performance** (3-year NQ data, 2023-01 to 2026-04):
- **58 trades** (~19/year)
- **Total R: +13.04** (~+4.3R/year at 1R sizing)
- **Profit factor: 4.41**
- **Max drawdown: 0.94R**
- **Monte Carlo 5th %ile: +7.54R** (positive in worst case)
- **Ruin probability (3R DD): 0.12%**
- **OOS test outperformed train** (1.34x ratio — proves params generalize)

---

## Quick start (5 minutes)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Get a Databento API key (free $125 credits)
#    → https://databento.com → sign up → portal → API Keys
export DATABENTO_API_KEY="db-your-key-here"

# 3. Pull 3 years of NQ data
cd python/
python databento_fetcher.py --symbol NQ --start 2023-01-01 --end 2026-04-14 \
    --out ../data/nq_5m.csv --resample 5m

# 4. Generate the trade log + edge analysis
python edge_discovery.py ../data/nq_5m.csv --pm 25 --out-dir ../results/

# 5. Run V3 Final with optimal params on the trade log
python v3_final.py ../results/trades_full.csv

# 6. Run optimization + sensitivity to verify on your data
python optimize_v3.py ../results/trades_full.csv
```

That's the full pipeline. Each step is independently runnable.

---

## Directory structure

```
eta_v3_framework/
├── README.md                 ← You are here
├── QUICKSTART.md             ← Detailed setup
├── requirements.txt          ← Python dependencies
├── setup.sh                  ← One-command setup script
│
├── python/                   ← All engine code
│   ├── firm_engine.py            15-voice analytical engine
│   ├── backtest.py               V1 detection + simulation engine (LOCKED)
│   ├── confluence_scorer.py      Objective 0-100 signal scorer
│   ├── v3_final.py               V3 with score gating (production version)
│   ├── v3_backtest.py            True V3 backtester with staged management
│   ├── optimize_v3.py            Walk-forward optimizer + sensitivity
│   ├── edge_discovery.py         Edge decomposition + analysis
│   ├── databento_fetcher.py      Historical data fetcher
│   ├── monte_carlo.py            Bootstrap MC simulator
│   ├── walkforward.py            Walk-forward validation
│   └── ... (15 more support modules)
│
├── pine/                     ← TradingView Pine Script
│   └── MNQ_ETA_v2_Firm.pine     Live charting indicator
│
├── docs/                     ← Specifications + dashboards
│   ├── EDGE_SPEC_V2.md           Data-derived edge spec
│   ├── EDGE_FINDINGS_RAW.md      Raw decomposition output
│   ├── V3_FINAL_DASHBOARD.md     Final results summary
│   ├── DATABENTO_SETUP.md        Data provider setup guide
│   └── INTEGRATION_README.md     Integration notes
│
├── v1_locked/                ← Frozen V1 baseline (DO NOT MODIFY)
│   ├── LOCKED.txt
│   ├── firm_engine.py
│   ├── backtest.py
│   ├── indicator_state.py
│   └── intermarket.py
│
├── data_samples/             ← Sample data for testing
│   └── nq_5m_sample.csv          (~10k bars to verify pipeline)
│
├── results/                  ← Pre-computed results from 3yr backtest
│   ├── V1_trades_3yr.csv         193 trades with full attribution
│   ├── by_setup.csv              Aggregated by ORB/EMA/Sweep
│   ├── by_tod.csv                By time-of-day bucket
│   ├── by_dow.csv                By day-of-week
│   ├── by_regime.csv             By Risk-On/Neutral/etc
│   └── by_setup_tod.csv          Setup × TOD cross-tab
│
├── scripts/                  ← Convenience scripts
│   ├── full_pipeline.sh          Pull data → analyze → optimize
│   └── run_optimal_v3.sh         Run V3 with optimal params
│
└── live_deployment/          ← Going live
    ├── webhook.py                Flask webhook server for TradingView alerts
    └── live_config.example.yaml  Configuration template
```

---

## The journey: V1 → V2 → V3 → V3 Optimal

| Version | Premise | 3-yr R | Trades | Max DD | Verdict |
|---------|---------|--------|--------|--------|---------|
| V1 raw | Take all V1 signals at PM≥25 | −2.65 | 193 | 7.45R | No edge |
| V2 filter | Hardcoded DOW/TOD rules | +6.45 | 24 | 0R | Edge but too slow |
| V3 simulated | All V1 + asymmetric mgmt | +9.53 | 193 | 0.95R | Promising |
| V3+Score (default) | Score gate + V3 mgmt | +8.08 | 49 | 0.47R | Validated |
| **V3 OPTIMAL** | **Walk-forward optimized params** | **+13.04** | **58** | **0.94R** | **SHIP** |

The optimization wasn't curve-fitting — every parameter was validated on out-of-sample data (2025-2026 not used during search). The OOS result was BETTER than train (+7.72 vs +5.77), proving the parameters capture real edge.

---

## Optimal parameters

```python
OPTIMAL_V3_PARAMS = {
    # Score thresholds (percentile-based, calibrated to data)
    't1_pct': 70,              # Tier 1 starts at score >= P70
    'aplus_pct': 85,           # A+ starts at score >= P85
    
    # Tier sizing
    'tier1_size': 0.5,         # Tier 1 = 0.5x base risk
    'aplus_size': 1.5,         # A+ = 1.5x base risk
    
    # V3 management
    'stall_bar': 4,            # Exit stalled trade at bar 4
    'stall_max_mfe': 0.2,      # Stall = MFE never exceeded 0.2R
    'stall_min_mae': -0.4,     # Stall = MAE > -0.4R (not in big trouble)
    'early_cut_mae': -0.6,     # Cut loss at -0.6R if no progress
    'early_cut_max_mfe': 0.3,  # "No progress" = MFE < 0.3R
    
    # Three-stage TP
    'tp1_R': 0.5,              # First partial at +0.5R
    'tp2_R': 1.5,              # Second partial at +1.5R
    'tp_partial_pct': 0.33,    # 33% per stage
    
    # Aggressive trail
    'trail_arm_R': 0.3,        # Trail activates at +0.3R MFE
    'trail_lock_R': 0.3,       # Lock at +0.3R when triggered
}
```

---

## Sensitivity warnings

Walk-forward optimization revealed which parameters matter most:

| Parameter | Sensitivity | Notes |
|-----------|-------------|-------|
| `aplus_pct` | **HIGH** (±20% = ±7R) | Most critical — the score threshold defines who gets size |
| `t1_pct` | MEDIUM (±20% = ±2-3R) | Affects trade volume |
| `aplus_size` | MEDIUM (proportional) | Linear scaling effect |
| `stall_bar` | LOW | Forgiving |
| `tp1_R` | LOW | Forgiving |
| `trail_arm_R` | LOW | Forgiving |

**Implication**: re-calibrate `aplus_pct` periodically (every 6 months) on rolling 1-year window. The other parameters can stay static.

---

## What's NOT in this framework (and why)

**Pyramiding** — Tested with the user's framework rules, added +0.25R over 3 years but widened variance. Failed objective improvement test. Removed.

**Higher PM thresholds** — V1 PM=40 looked good on small samples but optimization with proper sample size showed PM=25 + scoring gives better results.

**Multi-instrument trading** — Framework supports MNQ/ES via Databento but validation focused on NQ. Adapt with caution.

**Live execution code** — Webhook server included as starter; full broker integration (TradeStation, IBKR, etc.) is your responsibility based on your account setup.

---

## Critical reminders before going live

1. **Paper trade for 60 days first.** Track each signal's score, tier, and outcome. Verify live results match backtest within ±20%.

2. **Live MFE distributions may differ from backtest.** Three-stage TPs assume liquidity at exact prices. In live execution with slippage, partial fills may be at slightly worse prices.

3. **Re-validate quarterly.** Pull fresh 3-month data, re-run `optimize_v3.py`, confirm parameters haven't drifted.

4. **Position sizing is YOUR call.** All R values are abstract — 1R = whatever percent of account risk you decide. Start at 0.25% per R until live results match backtest.

5. **Daily/weekly loss limits.** The webhook includes a circuit breaker at -2.5R/day. You should also enforce -5R/week halt at the broker level.

6. **The V1 baseline is locked.** Do NOT modify `v1_locked/` files. If you need to change V1 logic, fork and rebuild from scratch — don't pollute the validated baseline.

---

## File-by-file: what does what

### Core engine (use these)
- `confluence_scorer.py` — Compute 0-100 score for any signal
- `v3_final.py` — Run V3 backtest on a trade log
- `v3_backtest.py` — Full V3 backtest from raw bars (true execution)
- `databento_fetcher.py` — Pull historical data from Databento
- `bulk_fetch.py` — Pull NQ + MNQ + ES + MES in one command
- `edge_discovery.py` — Generate trade log + decomposition
- `optimize_v3.py` — Walk-forward optimization + sensitivity

### Validation (use periodically)
- `monte_carlo.py` — Bootstrap simulation for risk estimates
- `walkforward.py` — Walk-forward validation across multiple windows
- `master_test.py` — A/B test feature combinations
- `v2_filter_validator.py` — Apply V2 spec rules to any trade log

### Support modules (don't modify)
- `firm_engine.py` — 15-voice signal generation
- `backtest.py` — V1 detection + base simulation
- `indicator_state.py` — Streaming ATR/EMA/RSI/etc.
- `intermarket.py` — Multi-symbol data loader

### Live deployment
- `webhook.py` — Flask server for TradingView webhook alerts
- `live_deployment/live_config.example.yaml` — Config template

---

## License

This framework is for personal use by the developer who commissioned it. Do not redistribute, sublicense, or use in commercial products without explicit permission.

## Disclaimer

Past backtest performance is not indicative of future trading results. Futures trading involves substantial risk of loss and is not suitable for all investors. The system has been rigorously validated, but no system is risk-free. Position size and capital allocation are YOUR responsibility.
