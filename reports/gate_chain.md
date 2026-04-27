# Gate chain — 2026-04-26T14:26:20.486397+00:00

**Chain verdict:** 🟢 ALLOW  ·  5 gates

| Gate | Verdict | Reason | Context |
|---|---|---|---|
| `heartbeat` | 🟢 ALLOW | alive | age_sec=15.186563 |
| `pre_trade_pause` | 🟢 ALLOW | no-state | — |
| `deadman` | 🟢 ALLOW | safe | age_sec=15.186803 |
| `correlation` | 🟢 ALLOW | within cap | agg_beta=1.0, cap=2.0, new=1×MNQ |
| `governor` | 🟢 ALLOW | ok | trades=0, streak=0, pnl=0 |

This report is read-only. Enforcement happens in
`src/mnq/executor/orders.py` via the chain itself.