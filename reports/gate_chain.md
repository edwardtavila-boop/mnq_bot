# Gate chain — 2026-04-17T01:39:42.687717+00:00

**Chain verdict:** 🟢 ALLOW  ·  5 gates

| Gate | Verdict | Reason | Context |
|---|---|---|---|
| `heartbeat` | 🟢 ALLOW | alive | age_sec=2.214836 |
| `pre_trade_pause` | 🟢 ALLOW | no-state | — |
| `deadman` | 🟢 ALLOW | safe | age_sec=2.21612 |
| `correlation` | 🟢 ALLOW | within cap | agg_beta=1.0, cap=2.0, new=1×MNQ |
| `governor` | 🟢 ALLOW | ok | trades=0, streak=0, pnl=0.0 |

This report is read-only. Enforcement happens in
`src/mnq/executor/orders.py` via the chain itself.