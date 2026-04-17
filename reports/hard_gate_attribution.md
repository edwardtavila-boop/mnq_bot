# Hard-Gate Attribution — Which Days Get Blocked?

Config: skip=0.5, reduce=0.67
Sample: 200 days

## Action breakdown

| Action | Days | PnL | Avg PnL/day |
|---|---:|---:|---:|
| Full | 178 | $+124.50 | $+0.70 |
| Reduced | 22 | $+28.00 | $+1.27 |
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
| 0.50–0.67 | 22 |
| 0.67–0.83 | 55 |
| 0.83–1.0 | 123 |

## Gate failure frequency (all days)

| Gate | Failures | Rate |
|---|---:|---:|
| orderflow | 108 | 54.0% |
| regime | 102 | 51.0% |
| trend_align | 91 | 45.5% |
| vol_band | 77 | 38.5% |
| session | 10 | 5.0% |
| cross_mag | 10 | 5.0% |
| time_of_day | 3 | 1.5% |

## Interpretation

Equal winner/loser blocking — the gates have no directional edge at this threshold. The pass_rate is noise with respect to PnL.
