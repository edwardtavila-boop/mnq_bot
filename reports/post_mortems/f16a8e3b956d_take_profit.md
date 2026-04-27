# Post-Mortem — f16a8e3b956d4accafcfa340d9cf4d09 (WIN)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-24T14:27:00+00:00 against a `trend_down` tape. Exit via `take_profit` at 19934.50 vs entry 19930.00.

## Evidence

- PnL: $+8.26 (commission $+0.74)
- Entry slippage: +0.0 ticks; exit slippage: +0.0 ticks.
- Regime at entry: `trend_down`; qty: 1.

## Red Team's primary dissent

- No automatic dissent triggered — trade failed cleanly within the stated risk envelope. This is the class of loss the system is designed to absorb.

## Resolution

[x] Accepted as surviving risk — monitoring: next 5 trades in this bucket.

## Falsification

I abandon this setup if ANY of:

- In the `trend_down` / `long` bucket after the next 5 closed trades, win-rate must be ≥ 100% (current: 100.0% on n=4).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 5 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `trend_down` / `long` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = f16a8e3b956d4accafcfa340d9cf4d09
side               = long
entry_ts / exit_ts = 2026-01-24T14:27:00+00:00 → 2026-01-24T14:29:00+00:00
entry / exit price = 19930.00 → 19934.50
pnl_dollars        = +8.2600
commission         = +0.7400
exit_reason        = take_profit
regime             = trend_down
slippage_ticks     = +0.00
entry_slip_ticks   = +0.00
```
