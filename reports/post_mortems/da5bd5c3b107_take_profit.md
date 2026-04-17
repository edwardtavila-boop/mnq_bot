# Post-Mortem — da5bd5c3b1074c5387b26c8bfbe3f5fc (WIN)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-23T14:23:00+00:00 against a `chop` tape. Exit via `take_profit` at 19967.00 vs entry 19963.00.

## Evidence

- PnL: $+7.26 (commission $+0.74)
- Entry slippage: +1.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `chop`; qty: 1.

## Red Team's primary dissent

- No automatic dissent triggered — trade failed cleanly within the stated risk envelope. This is the class of loss the system is designed to absorb.

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
order_id           = da5bd5c3b1074c5387b26c8bfbe3f5fc
side               = long
entry_ts / exit_ts = 2026-01-23T14:23:00+00:00 → 2026-01-23T14:26:00+00:00
entry / exit price = 19963.00 → 19967.00
pnl_dollars        = +7.2600
commission         = +0.7400
exit_reason        = take_profit
regime             = chop
slippage_ticks     = +1.00
entry_slip_ticks   = +1.00
```
