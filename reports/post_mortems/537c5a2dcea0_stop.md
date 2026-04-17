# Post-Mortem — 537c5a2dcea042408479699c1b0b1ac0 (LOSS)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-03-25T14:12:00+00:00 against a `trend_down` tape. Exit via `stop` at 24426.75 vs entry 24437.25.

## Evidence

- PnL: $-21.74 (commission $+0.74)
- Entry slippage: +0.0 ticks; exit slippage: +2.0 ticks.
- Regime at entry: `trend_down`; qty: 1.

## Red Team's primary dissent

- Exit slippage +2.0 ticks — realized fill was meaningfully worse than reference; spread/liquidity was adverse.
- Full-stop exit — the entry thesis was broken by price inside the first timebox. Either the filter gauntlet missed the regime or the signal strength was below threshold.
- Long in trend_down regime — directional bias gate should have suppressed this signal. Check `allow_long` / trend_align_bars.

## Resolution

[ ] Fixed — how: adjust gauntlet; [x] Accepted as surviving risk — monitoring per falsification criteria; [ ] Overridden.

## Falsification

I abandon this setup if ANY of:

- In the `trend_down` / `long` bucket after the next 5 closed trades, win-rate must be ≥ 50% (current: 50.0% on n=2).
- Mean slippage for this bucket must be ≤ +1.5 ticks.
- Net PnL in this bucket across the next 5 trades must be positive; if not, this setup is retired.

## Monitoring

- First review: after the next 5 trades in the `trend_down` / `long` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = 537c5a2dcea042408479699c1b0b1ac0
side               = long
entry_ts / exit_ts = 2026-03-25T14:12:00+00:00 → 2026-03-25T14:13:00+00:00
entry / exit price = 24437.25 → 24426.75
pnl_dollars        = -21.7400
commission         = +0.7400
exit_reason        = stop
regime             = trend_down
slippage_ticks     = +2.00
entry_slip_ticks   = +0.00
```
