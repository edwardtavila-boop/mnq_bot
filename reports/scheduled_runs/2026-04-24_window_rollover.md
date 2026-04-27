# Scheduled Run — 2026-04-24 (window rollover)

Autonomous status check after Claude Code usage window rolled over.
No code changes made; only `pytest` + `run_all_phases.py` + this report.

## Summary

- **pytest:** 1275 passed / 2 skipped / 0 failed (49.23s) — green.
- **Phase sweep:** 67 ok / 12 fail / 79 total — see [run_all_phases.md](../run_all_phases.md).
- **Tradovate:** dormant per 2026-04-24 mandate; no live-routing changes touched.
- **Databento:** still locked per 2026-04-23 mandate; no pulls attempted.

## Failure classification

The 12 phase failures split into three buckets. None are pytest regressions.

### A. Databento data missing (7 failures) — BLOCKED by mandate

`C:\mnq_data\databento\mnq1_1m.csv` does not exist on this machine. Per the
locked Databento mandate (`memory/databento_mandate.md`, 3rd re-lock
2026-04-23) I did NOT pull, prompt, or work around. All seven fail at the
same `load_databento_days()` call site:

- `gauntlet_weight_sweep_full` (Phase 8)
- `hard_gate_sweep` (Phase 9)
- `hard_gate_attribution` (Phase 9)
- `gate_pnl_attribution` (Phase 10)
- `ow_validation` (Phase 11)
- `backtest_real` (Phase 12)
- `walk_forward` (Cross — symptom is "have 1, need >= 11" but root cause is the same empty cache)

Operator unblock: type `pull databento` or stage `mnq1_1m.csv` at the expected
path. Until then these stages will fail every sweep.

### B. Script regressions (4 failures) — fixable, not authorized in this run

These are real code regressions where the orchestrator expects a CLI
contract that the underlying script no longer provides. They are not
data-blocked.

| Stage | Phase | Root cause |
|---|---|---|
| `gauntlet_shadow` | 8 | `shadow_trader.py` argparse has only `--status/--days/--verbose`; orchestrator passes `--gauntlet --output …` |
| `shadow_v16` | 8 | same — orchestrator passes `--v16 --output …` |
| `gauntlet_stats` | 8 | `from shadow_trader import DaySummary` — symbol no longer exported |
| `firm_live_review` | 3 | `compute_confluence` is missing from `src/mnq/firm_runtime.py` (bridge shim contract drift) |

Fix surface is narrow:
- Re-add `--gauntlet`, `--v16`, `--output` to `scripts/shadow_trader.py` argparse + dispatch (or update `scripts/run_all_phases.py` stage definitions to match the current CLI).
- Re-export `DaySummary` from `scripts/shadow_trader.py`.
- Re-run `scripts/firm_bridge.py --integrate` to regenerate `firm_runtime.py` with `compute_confluence` (or add it to the Firm probe set if the bridge no longer detects it).

I did not attempt these fixes — task instructions limit writes to pytest /
run_all_phases.py / status report. Next interactive session can pick these
up; they look like one-line argparse adds and a re-export.

### C. Windows file-locking (1 failure)

| Stage | Phase | Root cause |
|---|---|---|
| `crash_recovery` | 1 | `tempfile.TemporaryDirectory` cleanup raises `WinError 32` because the SQLite WAL file is still mmap-locked when `__exit__` runs. Data integrity is fine — `events_recovered: 200, expected: 200, errors: [], OK: True` printed before the cleanup error. The script returns rc=1 only because of the teardown crash. |

Fix: wrap the cleanup in `try/except PermissionError` or add a
`gc.collect()` + `sqlite3.Connection.close()` before the `with` block exits.
Cosmetic — the durability assertion itself passes.

## What the next session should pick up

1. **Bridge re-integration.** Run `scripts/firm_bridge.py --integrate` and
   confirm `compute_confluence` lands in `firm_runtime.py`. If the Firm
   package no longer exposes it, decide whether to remove the
   `firm_live_review` consumer or add a shim export.
2. **Shadow-trader CLI reconciliation.** Decide direction: either add
   `--gauntlet/--v16/--output/DaySummary` back to `shadow_trader.py`, or
   prune those four stages from `run_all_phases.py`. Three stages depend
   on the same script — fix together.
3. **Crash-recovery teardown polish.** Trivial — close the SQLite handle
   explicitly before the temp dir cleanup.
4. **Optional: Databento.** Operator decision only. Mandate stays locked
   until the operator says otherwise.

## Artifacts generated this run

- [reports/run_all_phases.md](../run_all_phases.md) — full ledger
- All Phase A/B/C/D/E/F outputs (49 stages) refreshed — these are
  data-independent and ran cleanly
- This report
