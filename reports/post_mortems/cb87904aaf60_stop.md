# Post-Mortem — cb87904aaf604a2caff4802b860090d4 (LOSS)

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

LONG entry on 2026-01-06T14:03:00+00:00 against a `chop` tape. Exit via `stop` at 20001.25 vs entry 20004.75.

## Evidence

- PnL: $-7.74 (commission $+0.74)
- Entry slippage: +1.0 ticks; exit slippage: +1.0 ticks.
- Regime at entry: `chop`; qty: 1.

## Red Team's primary dissent

- Long in chop regime — directional bias gate should have suppressed this signal. Check `allow_long` / trend_align_bars.

## Resolution

[ ] Fixed — how: adjust gauntlet; [x] Accepted as surviving risk — monitoring per falsification criteria; [ ] Overridden.

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
order_id           = cb87904aaf604a2caff4802b860090d4
side               = long
entry_ts / exit_ts = 2026-01-06T14:03:00+00:00 → 2026-01-06T14:04:00+00:00
entry / exit price = 20004.75 → 20001.25
pnl_dollars        = -7.7400
commission         = +0.7400
exit_reason        = stop
regime             = chop
slippage_ticks     = +1.00
entry_slip_ticks   = +1.00
```
