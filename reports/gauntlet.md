# Gauntlet-12 — 2026-04-17T01:39:42.822878+00:00

**Verdict:** 🟢 ALLOW  ·  12/12 passed  ·  score=0.91

| # | Gate | Pass | Score | Detail |
|---:|---|---|---:|---|
| 1 | `session` | 🟢 | 1.00 | time=14:30:00, in_lunch=False |
| 2 | `time_of_day` | 🟢 | 1.00 | hour=14, bucket=green |
| 3 | `vol_band` | 🟢 | 0.20 | stdev=4.37, band=[3.0, 40.0] |
| 4 | `trend_align` | 🟢 | 1.00 | d_fast=0.68, d_slow=0.26, fast=21027.61, slow=21024.68 |
| 5 | `cross_mag` | 🟢 | 0.97 | mag=2.92, min=1.50 |
| 6 | `orderflow` | 🟢 | 1.00 | mode=proxy, net_body_vol=13.59 |
| 7 | `volume_confirm` | 🟢 | 1.00 | cur=228, sma20=204.00 |
| 8 | `streak` | 🟢 | 1.00 | loss_streak=0, max=3 |
| 9 | `news_window` | 🟢 | 1.00 | events=0 |
| 10 | `regime` | 🟢 | 1.00 | regime=trend_up, side=long |
| 11 | `correlation` | 🟢 | 0.82 | mode=precomputed, corr=0.82 |
| 12 | `spread` | 🟢 | 0.88 | spread_ticks=0.50, max=2.00 |

Context: synthetic uptrend (stub). Wire live bars via
``src/mnq/features`` to get real verdicts.