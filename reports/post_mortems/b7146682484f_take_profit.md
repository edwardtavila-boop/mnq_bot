# Post-Mortem — b7146682484f43e7bd10911743463c40 (WIN)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-15T15:18:00+00:00 against a `high_vol` tape. Exit via `take_profit` at 20038.00 vs entry 20034.50.

## Evidence

- PnL: $+6.26 (commission $+0.74)
- Entry slippage: +2.0 ticks; exit slippage: +2.0 ticks.
- Regime at entry: `high_vol`; qty: 1.

## Red Team's primary dissent

- Exit slippage +2.0 ticks — realized fill was meaningfully worse than reference; spread/liquidity was adverse.
- Entry slippage +2.0 ticks — we paid up to get in; the signal wasn't strong enough to justify chasing.

## Resolution

[x] Accepted as surviving risk — monitoring: next 5 trades in this bucket.

## Falsification

I abandon this setup if ANY of:

- In the `high_vol` / `long` bucket after the next 5 closed trades, win-rate must be ≥ 40% (current: 25.0% on n=8).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 8 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `high_vol` / `long` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = b7146682484f43e7bd10911743463c40
side               = long
entry_ts / exit_ts = 2026-01-15T15:18:00+00:00 → 2026-01-15T15:19:00+00:00
entry / exit price = 20034.50 → 20038.00
pnl_dollars        = +6.2600
commission         = +0.7400
exit_reason        = take_profit
regime             = high_vol
slippage_ticks     = +2.00
entry_slip_ticks   = +2.00
```
