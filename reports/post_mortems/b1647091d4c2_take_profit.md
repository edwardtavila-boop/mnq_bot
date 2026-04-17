# Post-Mortem — b1647091d4c24b508d1b7cb4da34ca4c (WIN)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-14T15:18:00+00:00 against a `chop` tape. Exit via `take_profit` at 19989.00 vs entry 19985.25.

## Evidence

- PnL: $+6.76 (commission $+0.74)
- Entry slippage: +2.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `chop`; qty: 1.

## Red Team's primary dissent

- Entry slippage +2.0 ticks — we paid up to get in; the signal wasn't strong enough to justify chasing.

## Resolution

[x] Accepted as surviving risk — monitoring: next 5 trades in this bucket.

## Falsification

I abandon this setup if ANY of:

- In the `chop` / `long` bucket after the next 5 closed trades, win-rate must be ≥ 75% (current: 75.0% on n=8).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 8 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `chop` / `long` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = b1647091d4c24b508d1b7cb4da34ca4c
side               = long
entry_ts / exit_ts = 2026-01-14T15:18:00+00:00 → 2026-01-14T15:22:00+00:00
entry / exit price = 19985.25 → 19989.00
pnl_dollars        = +6.7600
commission         = +0.7400
exit_reason        = take_profit
regime             = chop
slippage_ticks     = +1.00
entry_slip_ticks   = +2.00
```
