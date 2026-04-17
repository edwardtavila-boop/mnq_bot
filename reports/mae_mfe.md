# MAE / MFE Distribution · 2026-04-17 01:39:39 UTC

- trades: **37** · winners: 26
- mean MAE: **$2.30** / mean MFE: **$5.05**

## MAE percentiles (all trades)
| p10 | p25 | p50 | p75 | p90 |
|---:|---:|---:|---:|---:|
| $0.00 | $0.00 | $0.00 | $7.24 | $7.74 |

## MFE percentiles (all trades)
| p10 | p25 | p50 | p75 | p90 |
|---:|---:|---:|---:|---:|
| $0.00 | $0.00 | $6.76 | $7.26 | $7.76 |

## Suggestion
- Observed p75 MAE of winners ≈ **$0.00** — consider a stop of roughly 1.0 × this.
- Observed p50 MFE ≈ **$7.26** — TP1 at or below this captures the modal winner.

## MAE histogram (all trades)
```
    +0.00  ████████████████████████████████████  (26)
    +0.87                                        (0)
    +1.75                                        (0)
    +2.62                                        (0)
    +3.50                                        (0)
    +4.37                                        (0)
    +5.24                                        (0)
    +6.12                                        (0)
    +6.99  ███████████                           (8)
    +7.87  ████                                  (3)
```

## MFE histogram (all trades)
```
    +0.00  ████████████████████                  (11)
    +0.83                                        (0)
    +1.65                                        (0)
    +2.48                                        (0)
    +3.30                                        (0)
    +4.13                                        (0)
    +4.96                                        (0)
    +5.78  ██                                    (1)
    +6.61  ████████████████████████████████████  (20)
    +7.43  █████████                             (5)
```

_Note: excursion is proxied via realized PnL until tick-level streams are available (Phase C DOM integration)._