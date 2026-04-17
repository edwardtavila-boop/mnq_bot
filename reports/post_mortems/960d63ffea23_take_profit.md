# Post-Mortem — 960d63ffea2342bf8d5d7964ce3c9ba4 (WIN)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-09T15:18:00+00:00 against a `trend_up` tape. Exit via `take_profit` at 20080.25 vs entry 20076.00.

## Evidence

- PnL: $+7.76 (commission $+0.74)
- Entry slippage: +0.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `trend_up`; qty: 1.

## Red Team's primary dissent

- No automatic dissent triggered — trade failed cleanly within the stated risk envelope. This is the class of loss the system is designed to absorb.

## Resolution

[x] Accepted as surviving risk — monitoring: next 5 trades in this bucket.

## Falsification

I abandon this setup if ANY of:

- In the `trend_up` / `long` bucket after the next 5 closed trades, win-rate must be ≥ 100% (current: 100.0% on n=2).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 5 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `trend_up` / `long` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = 960d63ffea2342bf8d5d7964ce3c9ba4
side               = long
entry_ts / exit_ts = 2026-01-09T15:18:00+00:00 → 2026-01-09T15:20:00+00:00
entry / exit price = 20076.00 → 20080.25
pnl_dollars        = +7.7600
commission         = +0.7400
exit_reason        = take_profit
regime             = trend_up
slippage_ticks     = +1.00
entry_slip_ticks   = +0.00
```
