# Shadow Venue Run

- Filtered variant: `r5_real_wide_target`
- Days simulated: **15**
- Shadow fills journal: `/sessions/kind-keen-faraday/mnt/mnq_bot/data/shadow/fills.jsonl`
- Gauntlet pre-filter: **OFF**
- Total shadow PnL: **$+11.50**  (16 fills)

- Gate tally → full: **15**, reduced: **0**, skip: **0**

## Per-day shadow ledger

| Day | Regime | Raw trades | Gate | va | Δ | Raw PnL | Shadow PnL | Fills |
|---:|---|---:|:---:|---:|---:|---:|---:|---:|
| 0 | real_trend_down | 2 | full (1.00x) | 10 | +0.013 | $+18.50 | $+18.50 | 4 |
| 1 | real_high_vol | 0 | full (1.00x) | 8 | -0.013 | $+0.00 | $+0.00 | 0 |
| 2 | real_trend_down | 1 | full (1.00x) | 9 | +0.000 | $-22.50 | $-22.50 | 2 |
| 3 | real_trend_down | 0 | full (1.00x) | 8 | -0.013 | $+0.00 | $+0.00 | 0 |
| 4 | real_trend_down | 0 | full (1.00x) | 8 | -0.013 | $+0.00 | $+0.00 | 0 |
| 5 | real_trend_down | 0 | full (1.00x) | 8 | -0.013 | $+0.00 | $+0.00 | 0 |
| 6 | real_high_vol | 2 | full (1.00x) | 8 | -0.013 | $+18.50 | $+18.50 | 4 |
| 7 | real_trend_up | 0 | full (1.00x) | 9 | +0.000 | $+0.00 | $+0.00 | 0 |
| 8 | real_high_vol | 1 | full (1.00x) | 9 | +0.000 | $-22.50 | $-22.50 | 2 |
| 9 | real_chop | 0 | full (1.00x) | 8 | -0.013 | $+0.00 | $+0.00 | 0 |
| 10 | real_trend_up | 1 | full (1.00x) | 9 | +0.000 | $+40.50 | $+40.50 | 2 |
| 11 | real_trend_down | 0 | full (1.00x) | 9 | +0.000 | $+0.00 | $+0.00 | 0 |
| 12 | real_trend_up | 1 | full (1.00x) | 9 | +0.000 | $-21.00 | $-21.00 | 2 |
| 13 | real_chop | 0 | full (1.00x) | 9 | +0.000 | $+0.00 | $+0.00 | 0 |
| 14 | real_trend_up | 0 | full (1.00x) | 9 | +0.000 | $+0.00 | $+0.00 | 0 |

## Interpretation

The shadow venue mirrors what the Firm's filter + Apex gate WOULD route to a real broker, but writes fills only to `data/shadow/fills.jsonl`. Parity tooling (`mnq parity`) can diff this journal against a future paper-sim or live journal to verify the shadow path reproduces expected PnL byte-for-byte.
