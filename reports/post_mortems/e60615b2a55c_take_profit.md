# Post-Mortem — e60615b2a55c44f7897e7b6aaf849578 (WIN)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

SHORT entry on 2026-01-11T14:25:00+00:00 against a `high_vol` tape. Exit via `take_profit` at 19975.50 vs entry 19979.25.

## Evidence

- PnL: $+6.76 (commission $+0.74)
- Entry slippage: +2.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `high_vol`; qty: 1.

## Red Team's primary dissent

- Entry slippage +2.0 ticks — we paid up to get in; the signal wasn't strong enough to justify chasing.

## Resolution

[x] Accepted as surviving risk — monitoring: next 5 trades in this bucket.

## Falsification

I abandon this setup if ANY of:

- In the `high_vol` / `short` bucket after the next 5 closed trades, win-rate must be ≥ 40% (current: 33.3% on n=3).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 5 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `high_vol` / `short` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = e60615b2a55c44f7897e7b6aaf849578
side               = short
entry_ts / exit_ts = 2026-01-11T14:25:00+00:00 → 2026-01-11T14:26:00+00:00
entry / exit price = 19979.25 → 19975.50
pnl_dollars        = +6.7600
commission         = +0.7400
exit_reason        = take_profit
regime             = high_vol
slippage_ticks     = +1.00
entry_slip_ticks   = +2.00
```
