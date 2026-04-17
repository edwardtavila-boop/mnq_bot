# Shadow Venue Run

- Filtered variant: `r5_real_wide_target`
- Days simulated: **15**
- Shadow fills journal: `/sessions/kind-keen-faraday/mnt/mnq_bot/data/shadow/fills.jsonl`
- Gauntlet pre-filter: **ON**
- Total shadow PnL: **$+0.00**  (0 fills)

- Gate tally → full: **15**, reduced: **0**, skip: **0**
- Gauntlet → evaluated: **8**, passed: **0**, blocked: **8** (100.0% block rate)

## Per-day shadow ledger

| Day | Regime | Raw | Gate | va | Δ | G✓ | G✗ | Raw PnL | Shadow PnL | Fills |
|---:|---|---:|:---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | real_trend_down | 2 | full (1.00x) | 10 | +0.013 | 0 | 2 | $+18.50 | $+0.00 | 0 |
| 1 | real_high_vol | 0 | full (1.00x) | 8 | -0.013 | 0 | 0 | $+0.00 | $+0.00 | 0 |
| 2 | real_trend_down | 1 | full (1.00x) | 9 | +0.000 | 0 | 1 | $-22.50 | $+0.00 | 0 |
| 3 | real_trend_down | 0 | full (1.00x) | 8 | -0.013 | 0 | 0 | $+0.00 | $+0.00 | 0 |
| 4 | real_trend_down | 0 | full (1.00x) | 8 | -0.013 | 0 | 0 | $+0.00 | $+0.00 | 0 |
| 5 | real_trend_down | 0 | full (1.00x) | 8 | -0.013 | 0 | 0 | $+0.00 | $+0.00 | 0 |
| 6 | real_high_vol | 2 | full (1.00x) | 8 | -0.013 | 0 | 2 | $+18.50 | $+0.00 | 0 |
| 7 | real_trend_up | 0 | full (1.00x) | 9 | +0.000 | 0 | 0 | $+0.00 | $+0.00 | 0 |
| 8 | real_high_vol | 1 | full (1.00x) | 9 | +0.000 | 0 | 1 | $-22.50 | $+0.00 | 0 |
| 9 | real_chop | 0 | full (1.00x) | 8 | -0.013 | 0 | 0 | $+0.00 | $+0.00 | 0 |
| 10 | real_trend_up | 1 | full (1.00x) | 9 | +0.000 | 0 | 1 | $+40.50 | $+0.00 | 0 |
| 11 | real_trend_down | 0 | full (1.00x) | 9 | +0.000 | 0 | 0 | $+0.00 | $+0.00 | 0 |
| 12 | real_trend_up | 1 | full (1.00x) | 9 | +0.000 | 0 | 1 | $-21.00 | $+0.00 | 0 |
| 13 | real_chop | 0 | full (1.00x) | 9 | +0.000 | 0 | 0 | $+0.00 | $+0.00 | 0 |
| 14 | real_trend_up | 0 | full (1.00x) | 9 | +0.000 | 0 | 0 | $+0.00 | $+0.00 | 0 |

## Gauntlet gate failure breakdown

| Gate | Failures | % of evaluated |
|---|---:|---:|
| orderflow | 7 | 87.5% |
| regime | 5 | 62.5% |
| volume_confirm | 1 | 12.5% |
| cross_mag | 1 | 12.5% |

## Interpretation

The shadow venue mirrors what the Firm's filter + Apex gate WOULD route to a real broker, but writes fills only to `data/shadow/fills.jsonl`. Parity tooling (`mnq parity`) can diff this journal against a future paper-sim or live journal to verify the shadow path reproduces expected PnL byte-for-byte.

With gauntlet enabled, trades are additionally screened through the 12-gate filter before routing. Blocked trades never reach the shadow venue. Compare gated vs ungated runs to measure the gauntlet's impact on PnL and trade frequency.
