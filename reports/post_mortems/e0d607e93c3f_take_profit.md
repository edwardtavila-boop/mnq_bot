# Post-Mortem — e0d607e93c3f4fec89eaf6d0bdf397c6 (WIN)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-16T14:23:00+00:00 against a `range_bound` tape. Exit via `take_profit` at 19997.00 vs entry 19993.00.

## Evidence

- PnL: $+7.26 (commission $+0.74)
- Entry slippage: +1.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `range_bound`; qty: 1.

## Red Team's primary dissent

- No automatic dissent triggered — trade failed cleanly within the stated risk envelope. This is the class of loss the system is designed to absorb.

## Resolution

[x] Accepted as surviving risk — monitoring: next 5 trades in this bucket.

## Falsification

I abandon this setup if ANY of:

- In the `range_bound` / `long` bucket after the next 5 closed trades, win-rate must be ≥ 100% (current: 100.0% on n=1).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 5 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `range_bound` / `long` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = e0d607e93c3f4fec89eaf6d0bdf397c6
side               = long
entry_ts / exit_ts = 2026-01-16T14:23:00+00:00 → 2026-01-16T14:24:00+00:00
entry / exit price = 19993.00 → 19997.00
pnl_dollars        = +7.2600
commission         = +0.7400
exit_reason        = take_profit
regime             = range_bound
slippage_ticks     = +1.00
entry_slip_ticks   = +1.00
```
