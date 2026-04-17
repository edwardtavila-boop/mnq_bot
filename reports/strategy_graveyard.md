# Strategy Graveyard

Last updated: 2026-04-16

Variants that were tested, found inadequate, and retired. Each entry
records the hypothesis, the test result, and why it was killed — so we
never re-discover the same dead end.

## Retired Variants

### `v0_1_baseline` (original EMA9/21 cross)
- **Hypothesis**: Raw EMA9/EMA21 cross on MNQ 1m is a viable edge.
- **Test**: 15-day live_sim + bootstrap CI.
- **Result**: Expectancy CI includes zero (−$2.50 to +$14.50, n=8).
- **Killed**: Insufficient n; rolled into `r5_real_wide_target` with gauntlet.
- **Lesson**: Unfiltered EMA cross on 1m is noisy; needs regime + orderflow filter.

### `tight_target_variants` (r1–r4 narrow TP)
- **Hypothesis**: Tight take-profit (3–5 tick) captures micro-moves.
- **Test**: Walk-forward sweep on 15-day Databento sample.
- **Result**: All tight-TP variants CI include zero after slippage.
- **Killed**: Slippage sensitivity sweep (Batch 6C) showed 1-tick friction consumes 35% of edge. Tight TP variants can't survive realistic fills.
- **Lesson**: MNQ micro-scalps need sub-tick slippage or DOM-based entry timing.

### `high_vol_regime_long` (trading high-vol days)
- **Hypothesis**: High-vol days offer bigger moves; filter gauntlet can select entry.
- **Test**: Gauntlet A/B comparison with `real_high_vol` days included vs excluded.
- **Result**: Blocking high_vol days saved −$13.50 per day on average.
- **Killed**: gate_regime hard-blocks high_vol (score 0.0).
- **Lesson**: Volatility expansion != opportunity. Regime gate correctly identifies these as non-confirming.

## Graveyard Rules

1. Every variant retired here must have a falsification criterion written *before* the test.
2. Include the bootstrap CI or equivalent statistical test that justified the kill.
3. Never re-test a graveyard variant without a material change to the hypothesis.
4. "Material change" = new data source, new gate, or new execution model — not parameter tweaking.
