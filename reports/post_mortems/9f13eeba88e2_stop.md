# Post-Mortem — 9f13eeba88e2461593272020ca5e9fa6 (LOSS)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-15T14:28:00+00:00 against a `high_vol` tape. Exit via `stop` at 19999.25 vs entry 20002.75.

## Evidence

- PnL: $-7.74 (commission $+0.74)
- Entry slippage: +1.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `high_vol`; qty: 1.

## Red Team's primary dissent

- No automatic dissent triggered — trade failed cleanly within the stated risk envelope. This is the class of loss the system is designed to absorb.

## Resolution

[ ] Fixed — how: adjust gauntlet; [x] Accepted as surviving risk — monitoring per falsification criteria; [ ] Overridden.

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
order_id           = 9f13eeba88e2461593272020ca5e9fa6
side               = long
entry_ts / exit_ts = 2026-01-15T14:28:00+00:00 → 2026-01-15T14:29:00+00:00
entry / exit price = 20002.75 → 19999.25
pnl_dollars        = -7.7400
commission         = +0.7400
exit_reason        = stop
regime             = high_vol
slippage_ticks     = +1.00
entry_slip_ticks   = +1.00
```
