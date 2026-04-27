# Hard-Gate Attribution — Which Days Get Blocked?

Config: skip=0.5, reduce=0.67
Sample: 15 days

## Action breakdown

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 14 | $+34.00 | $+2.43 |
| Reduced | 1 | $-22.50 | $-22.50 |
| Skipped | 0 | $+0.00 | $+0.00 |

## Skipped day analysis

- Winners blocked: **0**
- Losers blocked: **0**
- Flat blocked: **0**
- PnL of blocked days: $+0.00

## Pass-rate distribution

| Bucket | Days |
|---|---:|
| 0.0–0.33 | 0 |
| 0.33–0.50 | 0 |
| 0.50–0.67 | 1 |
| 0.67–0.83 | 2 |
| 0.83–1.0 | 12 |

## Gate failure frequency (all days)

| Gate | Failures | Rate |
|---|---:|---:|
| regime | 11 | 73.3% |
| orderflow | 6 | 40.0% |
| vol_band | 4 | 26.7% |
| trend_align | 3 | 20.0% |
| session | 2 | 13.3% |
| cross_mag | 1 | 6.7% |

## Interpretation

Equal winner/loser blocking — the gates have no directional edge at this threshold. The pass_rate is noise with respect to PnL.
