# Post-Mortem — 826e2bd6bc05400f9e9e1fcd489f3a7e (LOSS)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-04-09T14:45:00+00:00 against a `trend_up` tape. Exit via `stop` at 24999.50 vs entry 25010.00.

## Evidence

- PnL: $-21.74 (commission $+0.74)
- Entry slippage: +1.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `trend_up`; qty: 1.

## Red Team's primary dissent

- Full-stop exit — the entry thesis was broken by price inside the first timebox. Either the filter gauntlet missed the regime or the signal strength was below threshold.

## Resolution

[ ] Fixed — how: adjust gauntlet; [x] Accepted as surviving risk — monitoring per falsification criteria; [ ] Overridden.

## Falsification

I abandon this setup if ANY of:

- In the `trend_up` / `long` bucket after the next 5 closed trades, win-rate must be ≥ 40% (current: 25.0% on n=4).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 5 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `trend_up` / `long` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = 826e2bd6bc05400f9e9e1fcd489f3a7e
side               = long
entry_ts / exit_ts = 2026-04-09T14:45:00+00:00 → 2026-04-09T14:46:00+00:00
entry / exit price = 25010.00 → 24999.50
pnl_dollars        = -21.7400
commission         = +0.7400
exit_reason        = stop
regime             = trend_up
slippage_ticks     = +1.00
entry_slip_ticks   = +1.00
```
