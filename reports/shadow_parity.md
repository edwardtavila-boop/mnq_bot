# Shadow → Sim Parity Report

**Mode:** DETERMINISTIC (zero-slip)
**Days:** 15
**Status:** PARITY OK

## Summary

- Sim trades: **1**
- Shadow fills: **2** (1 paired trades)
- Matched: **1** / Unmatched sim: **0** / Unmatched shadow: **0**
- Side mismatches: **0**

- Total sim PnL: **$-21.00**
- Total shadow PnL: **$-21.00**
- PnL difference: **$+0.00**
- Max per-trade diff: **$0.00**
- Avg per-trade diff: **$+0.0000**

## Per-Trade Comparison

| # | Day | Side | Sim PnL | Shadow PnL | Δ PnL | Match |
|---:|---|---|---:|---:|---:|:---:|
| 0 | day-0 | long | $-21.00 | $-21.00 | $+0.00 | ✓ |

## Interpretation

In deterministic mode (zero slippage, zero latency), shadow PnL should
match sim PnL exactly. Any non-zero diff indicates a routing or
price-resolution bug in the shadow venue layer.