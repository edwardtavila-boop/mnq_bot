# Post-Mortem — 1f297db36b9a41c2b169ba0bfd9cbf9b (LOSS)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

SHORT entry on 2026-03-23T15:20:00+00:00 against a `trend_down` tape. Exit via `stop` at 24625.50 vs entry 24615.00.

## Evidence

- PnL: $-21.74 (commission $+0.74)
- Entry slippage: +1.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `trend_down`; qty: 1.

## Red Team's primary dissent

- Full-stop exit — the entry thesis was broken by price inside the first timebox. Either the filter gauntlet missed the regime or the signal strength was below threshold.

## Resolution

[ ] Fixed — how: adjust gauntlet; [x] Accepted as surviving risk — monitoring per falsification criteria; [ ] Overridden.

## Falsification

I abandon this setup if ANY of:

- In the `trend_down` / `short` bucket after the next 5 closed trades, win-rate must be ≥ 40% (current: 0.0% on n=1).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 5 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `trend_down` / `short` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = 1f297db36b9a41c2b169ba0bfd9cbf9b
side               = short
entry_ts / exit_ts = 2026-03-23T15:20:00+00:00 → 2026-03-23T15:21:00+00:00
entry / exit price = 24615.00 → 24625.50
pnl_dollars        = -21.7400
commission         = +0.7400
exit_reason        = stop
regime             = trend_down
slippage_ticks     = +1.00
entry_slip_ticks   = +1.00
```
