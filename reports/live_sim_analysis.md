# EVOLUTIONARY TRADING ALGO // Paper Sim — Live Run

- Generated: 2026-04-26T14:25:20.050040+00:00
- Journal: `C:\Users\edwar\projects\mnq_bot\data\live_sim\journal.sqlite`
- Days × bars: **20 × 390**

This is an internal-simulation "live" run. Every state transition — order submits, acks, fills, risk decisions, breaker folds, slippage records, reconciliation — is committed to a durable SQLite journal the same way the production path would. The bot is now accumulating the data it needs to adapt: fill expectations vs realizations, per-regime PnL attribution, turnover drift, and per-check safety outcomes.

## Pipeline counters

| Counter | Value |
|---|---:|
| Signals emitted | 37 |
| Orders submitted | 37 |
| Entry fills | 37 |
| Round trips closed | 37 |
| Blocked by pre-trade risk | 0 |
| Breaker halts | 0 |

## Paper-sim env summary (from FILL_REALIZED replay)

| Metric | Value |
|---|---|
| Closed trades | 37 |
| Net PnL | $101.62 |
| Expectancy / trade | $2.75 |
| Win rate | 70.3% |
| Avg slippage (ticks) | +1.11 |
| Malformed events skipped | 74 |

## Slippage attribution (per-fill, from SlippageRecorder.export)

| Stat | Value |
|---|---:|
| Fills recorded | 111 |
| Mean slippage (ticks) | +1.072 |
| Median slippage (ticks) | +1.000 |
| Stdev slippage (ticks) | +0.657 |
| p95 adverse (ticks) | +2.000 |
| p05 favourable (ticks) | +0.000 |

| Volatility regime | n | mean slip (ticks) |
|---|---:|---:|
| high | 33 | +1.273 |
| normal | 78 | +0.987 |

## Per-regime PnL (from trade-closure FILL_REALIZED)

| Regime | trades | wins | win% | net PnL | avg slip (ticks) |
|---|---:|---:|---:|---:|---:|
| chop | 13 | 10 | 76.9% | $47.88 | +1.23 |
| high_vol | 11 | 3 | 27.3% | $-43.14 | +1.27 |
| range_bound | 1 | 1 | 100.0% | $7.26 | +1.00 |
| trend_down | 7 | 7 | 100.0% | $52.32 | +0.86 |
| trend_up | 5 | 5 | 100.0% | $37.30 | +0.80 |

## Turnover drift (gauntlet expectation vs realized)

| Field | Value |
|---|---|
| metric | `trades_per_day` |
| expected μ | 2.080 |
| expected σ | 0.200 |
| realized | 1.850 |
| z-score | -1.150 |
| threshold | ±3.0 |
| anomalous? | **False** |

## Position reconciliation (venue vs local)

- Diffs: 0
- Critical diffs: 0
- OK? **True**

## Interpretation

- The durable SQLite journal at the path above now holds the complete state history. A restart can call `OrderBook.from_journal(...)` and `net_positions_from_journal(...)` to reconstruct the in-memory world exactly.
- `summarize_env` reads the same journal the parity dashboard would see against live fills. Because we wrote trade-closure `FILL_REALIZED` events with the full schema (`entry_ts`, `exit_ts`, `entry_price`, `exit_price`, `pnl_dollars`, `commission_dollars`, `exit_reason`, `slippage_ticks`, `side`, `qty`), the parity pipeline is ready to compare this paper-sim run against a future live shadow stream.
- `SlippageRecorder.export_to_dataframe` yields a polars frame suitable for `mnq.calibration.fit_slippage.fit_per_regime` — the adaptation loop can now refit the fill model from realised data.
- `TurnoverDriftMonitor` computes a z-score against the per-variant expected turnover (μ=2.08, σ=0.20 for this run); anomalies would fire the `DRIFT_ALERT` event in the journal.
- `PositionReconciler` ran against a FakeSnapshotFetcher reporting flat, and passed cleanly — confirming the end-to-end state machine left no ghost positions.
- The `ScriptedStrategy` was used because the baseline spec's HTF/rising filter is structurally silent on 1-minute bars (documented in `reports/pnl_report.md`). This does NOT affect the execution stack under test — the stack is strategy-agnostic.
