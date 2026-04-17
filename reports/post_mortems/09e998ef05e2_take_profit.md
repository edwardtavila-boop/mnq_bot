# Post-Mortem — 09e998ef05e241faa051b72e2c98122b (WIN)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-19T14:23:00+00:00 against a `chop` tape. Exit via `take_profit` at 19981.50 vs entry 19977.75.

## Evidence

- PnL: $+6.76 (commission $+0.74)
- Entry slippage: +1.0 ticks; exit slippage: +2.0 ticks.
- Regime at entry: `chop`; qty: 1.

## Red Team's primary dissent

- Exit slippage +2.0 ticks — realized fill was meaningfully worse than reference; spread/liquidity was adverse.

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
order_id           = 09e998ef05e241faa051b72e2c98122b
side               = long
entry_ts / exit_ts = 2026-01-19T14:23:00+00:00 → 2026-01-19T14:25:00+00:00
entry / exit price = 19977.75 → 19981.50
pnl_dollars        = +6.7600
commission         = +0.7400
exit_reason        = take_profit
regime             = chop
slippage_ticks     = +2.00
entry_slip_ticks   = +1.00
```
