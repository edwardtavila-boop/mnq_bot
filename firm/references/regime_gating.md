# Regime-Gating (Tier 3+)

Not every agent weighs in on every decision. A regime-detector classifies current state, and only agents competent in that regime are summoned.

## Five axes, discrete states

**Volatility:** Compressed (VIX<15) / Normal (15-20) / Elevated (20-25) / Crisis (>25)
**Trend:** Strong (ADX>25, >60% above 200DMA) / Weak / Chop (ADX<15)
**Liquidity:** Deep / Normal / Thin (wide spreads, funding stress)
**Correlation:** Normal / Elevated / Extreme (eigenvalue-1 of cross-asset matrix)
**Macro quadrant:** Growth ↑↓ × Inflation ↑↓

## Collapse to 8–12 canonical regimes

Do not multiply axes (4×3×3×3×4 = 432 states, impossibly sparse). Instead collapse to regimes that actually matter for your strategies:

| # | Regime | Thrives |
|---|--------|---------|
| 1 | Low-vol trend | Breakout, trend continuation |
| 2 | Low-vol chop | Mean reversion, range trading |
| 3 | Normal-vol trend | Most strategies |
| 4 | Normal-vol chop | Fade extremes |
| 5 | Elevated-vol trend | Reduced size trend |
| 6 | Elevated-vol chop | Stand aside or fade |
| 7 | Crisis risk-off | Shorts, vol longs, gold |
| 8 | Crisis risk-on rebound | Sharp long trades |
| 9 | Transition | **Reduce size, max dissent** |

If you can't explain in one sentence why a regime matters, it's curve-fit.

## The competence matrix

For each (agent × regime) pair, track historical calibration. Becomes a lookup table:

| Agent | Low-vol trend | Low-vol chop | Crisis | Transition |
|-------|---------------|--------------|--------|------------|
| Quant | 1.4× | 0.9× | 0.6× | 0.5× |
| Red Team | 1.0× | 1.2× | 1.5× | 1.5× |
| Macro | 1.1× | 0.7× | 1.4× | 0.8× |

Built empirically from the calibration layer. You don't assign it — you measure it.

## Transitions — where strategies die

Regime transitions are categorically more dangerous than regimes themselves. Most strategies die between regimes, not in them.

**Transition rules:**
- Position size cut 50%
- Red Team weight × 1.5
- No new strategies deployed
- Existing strategies reviewed for regime-match
- Kill-switch thresholds tightened 25%

**Detection:** when two or more axes show conflicting signals (vol rising but trend still strong, etc.), flag transition.

## Workflow

```
Signal → Regime Detector → Transition Check → Agent Selector →
Sealed Submissions → Simultaneous Reveal → Weighted Synthesis → Decision
```

## Avoid overfitting the regime classifier

If you add regimes by looking at past blowups ("let me add a regime for March 2020"), you're curve-fitting the classifier, not the strategy. Keep regimes economically motivated and stable over time.
