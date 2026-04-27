# Post-Mortem — 7fafe4988c464777b5a1ee295f755546 (LOSS)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

SHORT entry on 2026-01-21T14:23:00+00:00 against a `high_vol` tape. Exit via `stop` at 20066.50 vs entry 20062.75.

## Evidence

- PnL: $-8.24 (commission $+0.74)
- Entry slippage: +1.0 ticks; exit slippage: +2.0 ticks.
- Regime at entry: `high_vol`; qty: 1.

## Red Team's primary dissent

- Exit slippage +2.0 ticks — realized fill was meaningfully worse than reference; spread/liquidity was adverse.

## Resolution

[ ] Fixed — how: adjust gauntlet; [x] Accepted as surviving risk — monitoring per falsification criteria; [ ] Overridden.

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
order_id           = 7fafe4988c464777b5a1ee295f755546
side               = short
entry_ts / exit_ts = 2026-01-21T14:23:00+00:00 → 2026-01-21T14:24:00+00:00
entry / exit price = 20062.75 → 20066.50
pnl_dollars        = -8.2400
commission         = +0.7400
exit_reason        = stop
regime             = high_vol
slippage_ticks     = +2.00
entry_slip_ticks   = +1.00
```
