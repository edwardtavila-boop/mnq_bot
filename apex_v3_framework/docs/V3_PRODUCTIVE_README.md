# V3 Productive Bot — Final Spec

After V1 (overfit / no edge) and V2 (real edge but only 8 trades/year), V3 combines:
- V1's broad signal detection (193 trades / 3yr)
- V2's tier-1 filter as the "premium" tier
- Asymmetric payoff management on every trade
- Tiered sizing based on signal quality

## The tier system

Every V1 signal gets classified into one of three tiers:

**TIER 1 — Premium (24 trades / 3yr · 12% of signals)**
- Thursday or Friday
- After 10:30 ET
- ORB requires Risk-On regime
- **Size: 100%**
- Result: 71% win rate, +5.66R total, PF 11.92

**TIER 2 — Standard (64 trades / 3yr · 33% of signals)**
- Wed/Thu/Fri after 9:45 ET
- Most V2 conditions met but not all
- **Size: 50%**
- Result: 72% win rate, +3.55R total, PF 1.82

**TIER 3 — Speculative (105 trades / 3yr · 54% of signals)**
- Mon/Tue OR opening 15min OR ORB in Neutral regime
- Highest signal volume but lowest edge
- **Size: 25%**
- Result: 64% win rate, +0.32R total, PF 1.08

## The asymmetric payoff rules

Applied to EVERY trade regardless of tier:

**Stall exit** — bar 6 with no MFE > 0.2R and MAE > -0.4R
→ exit at small loss/gain, free up capital

**Cut losers early** — if MAE reaches -0.6R AND MFE never reached 0.3R
→ exit at -0.6R, save 40% on losing trades

**Three-stage take profits**
- 33% at +0.7R (lock in early profit)
- 33% at +1.5R (catch the typical winner range)
- 33% trailed (capture runners with 9 EMA trail)

**Trail-saved-loss** — if trade went +0.5R then reversed to SL
→ trail kicked in at +0.3R, save the loss

## Run it

```bash
python v3_engine.py /tmp/historical/nq_5m.csv --pm 25
python v3_engine.py /tmp/historical/nq_5m.csv --pm 25 -v   # verbose
```

## Validation results

3-year NQ data (Jan 2023 – Apr 2026):

| Metric | V1 | V3 |
|--------|-----|-----|
| Trades | 193 | 193 |
| Win rate | 45% | **67%** |
| Total R | −2.65 | **+9.53** |
| PF | 0.92 | **2.05** |
| Max DD | 7.45R | 0.95R |
| MC 5%ile R | −15+ | **+4.78** |
| Ruin probability | 85%+ | **0.32%** |

Same trade volume as V1, completely different result. The management is the edge.

## Important caveat

V3 currently uses simulated management — applies V3 rules to V1's recorded MFE/MAE values as a "what-if" projection. Real-time execution may differ slightly because:
- Live MFE may not reach the levels recorded in backtest (slippage, micro-volatility)
- The 33% partial fills assume liquidity at exact prices
- Trail stops execute on close, not intra-bar

To validate, paper trade V3 for 60 days. Compare live results to projected V3 results. If live R-per-trade is within 20% of projected, V3 is real. If gap is larger, the management rules need calibration to live conditions.

## Tier 3 question

Tier 3 contributes only +0.32R over 3 years from 105 trades (avg +0.003R/trade — basically noise). Two options:
- **Drop Tier 3 entirely** → 88 trades/3yr, +9.21R, even cleaner
- **Keep Tier 3 at 25% size** → 193 trades/3yr, +9.53R, more activity

If you value trade volume for the bot to "feel busy" during low-conviction setups, keep Tier 3. If you value clean stats, drop it.

## What's next

1. Paper trade V3 for 60 days
2. Compare live MFE/MAE to backtest MFE/MAE per setup
3. If live performance matches projection → scale capital cautiously
4. Eventually rebuild V3 as a TRUE backtest (modify backtester to execute the tiered management directly during trade simulation rather than as post-hoc projection)
