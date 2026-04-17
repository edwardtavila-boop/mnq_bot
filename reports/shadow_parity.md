# Shadow → Sim Parity Report

**Mode:** DETERMINISTIC (zero-slip)
**Days:** 15
**Status:** PARITY OK

## Summary

- Sim trades: **8**
- Shadow fills: **16** (8 paired trades)
- Matched: **8** / Unmatched sim: **0** / Unmatched shadow: **0**
- Side mismatches: **0**

- Total sim PnL: **$+11.50**
- Total shadow PnL: **$+11.50**
- PnL difference: **$+0.00**
- Max per-trade diff: **$0.00**
- Avg per-trade diff: **$+0.0000**

## Per-Trade Comparison

| # | Day | Side | Sim PnL | Shadow PnL | Δ PnL | Match |
|---:|---|---|---:|---:|---:|:---:|
| 0 | day-0 | long | $+40.00 | $+40.00 | $+0.00 | ✓ |
| 1 | day-0 | short | $-21.50 | $-21.50 | $+0.00 | ✓ |
| 2 | day-2 | long | $-22.50 | $-22.50 | $+0.00 | ✓ |
| 3 | day-6 | long | $-20.50 | $-20.50 | $+0.00 | ✓ |
| 4 | day-6 | short | $+39.00 | $+39.00 | $+0.00 | ✓ |
| 5 | day-8 | long | $-22.50 | $-22.50 | $+0.00 | ✓ |
| 6 | day-10 | long | $+40.50 | $+40.50 | $+0.00 | ✓ |
| 7 | day-12 | long | $-21.00 | $-21.00 | $+0.00 | ✓ |

## Interpretation

In deterministic mode (zero slippage, zero latency), shadow PnL should
match sim PnL exactly. Any non-zero diff indicates a routing or
price-resolution bug in the shadow venue layer.