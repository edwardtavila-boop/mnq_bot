# Run-All-Phases — 2026-04-17 01:40:23 UTC

- Stages: **79**
- Passed: **72/79**

## Per-phase summary

| Phase | Passed | Total |
|---|---:|---:|
| Cross | 2 | 2 |
| Phase 0 | 1 | 2 |
| Phase 1 | 2 | 2 |
| Phase 10 | 1 | 1 |
| Phase 11 | 1 | 1 |
| Phase 12 | 2 | 2 |
| Phase 2 | 1 | 1 |
| Phase 3 | 6 | 7 |
| Phase 5 | 1 | 1 |
| Phase 7 | 1 | 1 |
| Phase 8 | 4 | 8 |
| Phase 9 | 2 | 2 |
| Phase A | 9 | 9 |
| Phase B | 10 | 10 |
| Phase C | 10 | 10 |
| Phase D | 10 | 10 |
| Phase E | 4 | 4 |
| Phase F | 5 | 6 |

## Stage ledger

| # | Phase | Stage | Status | Duration (s) |
|---:|---|---|---|---:|
| 1 | Phase 0 | `firm_bridge` | FAIL (rc=2) | 0.0 |
| 2 | Phase 0 | `live_sim` | OK | 2.5 |
| 3 | Phase 2 | `replay_journal` | OK | 1.3 |
| 4 | Phase 1 | `crash_recovery` | OK | 0.4 |
| 5 | Cross | `strategy_registry` | OK | 1.0 |
| 6 | Phase 3 | `strategy_ab` | OK | 1.8 |
| 7 | Cross | `walk_forward` | OK | 1.4 |
| 8 | Phase 3 | `firm_vs_baseline` | OK | 1.4 |
| 9 | Phase 3 | `firm_vs_baseline_apex_real` | OK | 2.0 |
| 10 | Phase 8 | `shadow_trader` | FAIL (rc=1) | 1.2 |
| 11 | Phase 8 | `shadow_parity` | OK | 1.3 |
| 12 | Phase 8 | `gauntlet_shadow` | FAIL (rc=2) | 1.3 |
| 13 | Phase 8 | `gauntlet_stats` | FAIL (rc=1) | 1.2 |
| 14 | Phase 8 | `shadow_v16` | FAIL (rc=2) | 1.2 |
| 15 | Phase 8 | `gauntlet_weight_sweep` | OK | 6.2 |
| 16 | Phase 8 | `shadow_sensitivity` | OK | 2.1 |
| 17 | Phase 8 | `gauntlet_weight_sweep_full` | OK | 69.6 |
| 18 | Phase 9 | `hard_gate_sweep` | OK | 7.7 |
| 19 | Phase 9 | `hard_gate_attribution` | OK | 7.7 |
| 20 | Phase 10 | `gate_pnl_attribution` | OK | 7.7 |
| 21 | Phase 11 | `ow_validation` | OK | 7.5 |
| 22 | Phase 7 | `rolling_calibration` | OK | 1.0 |
| 23 | Phase 3 | `calibration` | OK | 1.1 |
| 24 | Phase 5 | `bayesian_expectancy` | OK | 0.1 |
| 25 | Phase 3 | `firm_review_markdown` | OK | 1.4 |
| 26 | Phase 3 | `firm_live_review` | FAIL (rc=3) | 1.2 |
| 27 | Phase 3 | `postmortem` | OK | 0.2 |
| 28 | Phase 1 | `daily_digest` | OK | 0.1 |
| 29 | Phase A | `alerting` | OK | 0.1 |
| 30 | Phase A | `edge_decay` | OK | 0.0 |
| 31 | Phase A | `mae_mfe` | OK | 0.0 |
| 32 | Phase A | `time_heatmap` | OK | 0.0 |
| 33 | Phase A | `counterfactual` | OK | 0.0 |
| 34 | Phase A | `trade_governor` | OK | 0.0 |
| 35 | Phase A | `rule_adherence` | OK | 0.0 |
| 36 | Phase A | `email_recap` | OK | 0.1 |
| 37 | Phase A | `time_exit` | OK | 0.0 |
| 38 | Phase B | `psych_sidecar` | OK | 0.1 |
| 39 | Phase B | `pre_trade_pause` | OK | 0.0 |
| 40 | Phase B | `loss_streak` | OK | 0.0 |
| 41 | Phase B | `hot_hand` | OK | 0.0 |
| 42 | Phase B | `auto_screenshot` | OK | 0.1 |
| 43 | Phase B | `voice_memo_list` | OK | 0.0 |
| 44 | Phase B | `weekly_review` | OK | 0.0 |
| 45 | Phase B | `monthly_narrative` | OK | 0.0 |
| 46 | Phase B | `mistake_taxonomy` | OK | 0.0 |
| 47 | Phase B | `ai_reviewer` | OK | 0.0 |
| 48 | Phase C | `cumulative_delta` | OK | 0.0 |
| 49 | Phase C | `volume_profile` | OK | 0.0 |
| 50 | Phase C | `sector_rotation` | OK | 0.0 |
| 51 | Phase C | `news_feed` | OK | 0.0 |
| 52 | Phase C | `gex_monitor` | OK | 0.0 |
| 53 | Phase C | `vix_term` | OK | 0.0 |
| 54 | Phase C | `breadth_monitor` | OK | 0.0 |
| 55 | Phase C | `event_calendar` | OK | 0.0 |
| 56 | Phase C | `earnings_amp` | OK | 0.0 |
| 57 | Phase C | `seasonality` | OK | 0.0 |
| 58 | Phase D | `gbm_filter` | OK | 0.1 |
| 59 | Phase D | `shap_rank` | OK | 0.1 |
| 60 | Phase D | `trade_clusters` | OK | 0.0 |
| 61 | Phase D | `anomaly_detect` | OK | 0.0 |
| 62 | Phase D | `heartbeat` | OK | 0.0 |
| 63 | Phase D | `deadman_switch` | OK | 0.0 |
| 64 | Phase D | `encrypted_backup` | OK | 2.0 |
| 65 | Phase D | `tax_1256` | OK | 0.0 |
| 66 | Phase D | `pretrade_checklist` | OK | 0.0 |
| 67 | Phase D | `correlation_cap` | OK | 0.0 |
| 68 | Phase E | `gate_chain_check` | OK | 0.0 |
| 69 | Phase E | `backtest_baseline_export` | OK | 0.0 |
| 70 | Phase E | `parity_harness` | OK | 0.0 |
| 71 | Phase E | `gauntlet_check` | OK | 0.0 |
| 72 | Phase F | `burn_in_72h` | FAIL (rc=1) | 0.1 |
| 73 | Phase F | `eta_v3_probe` | OK | 0.1 |
| 74 | Phase F | `eta_v3_enrich` | OK | 0.1 |
| 75 | Phase F | `eta_v3_bridge` | OK | 0.1 |
| 76 | Phase F | `eta_v3_meta` | OK | 0.1 |
| 77 | Phase F | `eta_meta_orchestrator` | OK | 0.1 |
| 78 | Phase 12 | `backtest_real` | OK | 40.4 |
| 79 | Phase 12 | `backtest_real_analysis` | OK | 0.2 |

## Failures

### `firm_bridge` (Phase 0)

```
# stdout (last 10 lines)
## Gaps blocking integration

_none — contract satisfied._

## Next step

Continue running the markdown-only Firm review path (`scripts/firm_review.py`). Rerun this probe after each Firm-code fine-tune cycle; integration will auto-enable when the contract is met.

wrote /sessions/kind-keen-faraday/mnt/mnq_bot/reports/firm_integration.md
wrote /sessions/kind-keen-faraday/mnt/mnq_bot/reports/firm_integration.json
# stderr (last 10 lines)
NOT INTEGRATING: readiness probe failed.
```

### `shadow_trader` (Phase 8)

```
# stdout (last 10 lines)
<empty>
# stderr (last 10 lines)
    return future.result()
  File "/sessions/kind-keen-faraday/mnt/mnq_bot/scripts/shadow_trader.py", line 320, in async_main
    result = await trader.run_session()
  File "/sessions/kind-keen-faraday/mnt/mnq_bot/scripts/shadow_trader.py", line 176, in run_session
    journal = EventJournal(SHADOW_JOURNAL_PATH)
  File "/sessions/kind-keen-faraday/mnt/mnq_bot/src/mnq/storage/journal.py", line 54, in __init__
    self._init_db()
  File "/sessions/kind-keen-faraday/mnt/mnq_bot/src/mnq/storage/journal.py", line 69, in _init_db
    conn.execute("""
sqlite3.OperationalError: disk I/O error
```

### `gauntlet_shadow` (Phase 8)

```
# stdout (last 10 lines)
<empty>
# stderr (last 10 lines)
usage: shadow_trader.py [-h] [--status] [--days DAYS] [--verbose]
shadow_trader.py: error: unrecognized arguments: --gauntlet --output reports/shadow_venue_gated.md
```

### `gauntlet_stats` (Phase 8)

```
# stdout (last 10 lines)
<empty>
# stderr (last 10 lines)
Traceback (most recent call last):
  File "/sessions/kind-keen-faraday/mnt/mnq_bot/scripts/gauntlet_stats.py", line 30, in <module>
    from shadow_trader import (  # noqa: E402
ImportError: cannot import name 'DaySummary' from 'shadow_trader' (/sessions/kind-keen-faraday/mnt/mnq_bot/scripts/shadow_trader.py)
```

### `shadow_v16` (Phase 8)

```
# stdout (last 10 lines)
<empty>
# stderr (last 10 lines)
usage: shadow_trader.py [-h] [--status] [--days DAYS] [--verbose]
shadow_trader.py: error: unrecognized arguments: --v16 --output reports/shadow_venue_v16.md
```

### `firm_live_review` (Phase 3)

```
# stdout (last 10 lines)
<empty>
# stderr (last 10 lines)
firm_runtime shim not present — run `python scripts/firm_bridge.py --integrate` first.
  detail: No module named 'firm'
```

### `burn_in_72h` (Phase F)

```
# stdout (last 10 lines)
<empty>
# stderr (last 10 lines)
  File "/sessions/kind-keen-faraday/mnt/mnq_bot/scripts/burn_in_72h.py", line 298
    sys.exit(main
            ^
SyntaxError: '(' was never closed
```

## Output index

- `reports/live_sim_analysis.md` — Phase 0 internal-sim summary
- `reports/replay_audit.md` — Phase 2 determinism
- `reports/crash_recovery.md` — Phase 1 durability
- `reports/strategy_v2_report.md` — Phase 3 A/B winner
- `reports/walk_forward.md` — Cross-cutting out-of-sample edge
- `reports/firm_vs_baseline.md` — Phase 3 Firm filter justification (synthetic Apex)
- `reports/firm_vs_baseline_apex_real.md` — Phase 3 Firm filter (real Apex — Batch 3F/3H)
- `reports/shadow_venue.md` — Phase 8 Shadow-venue dry-run (Batch 4A — scaffold)
- `reports/shadow_parity.md` — Phase 8 Shadow→Sim parity check (Batch 4C)
- `reports/shadow_venue_gated.md` — Phase 8 Shadow-venue with gauntlet pre-filter (Batch 5A)
- `reports/gauntlet_stats.md` — Phase 8 Gauntlet A/B comparison (Batch 5A)
- `reports/shadow_venue_v16.md` — Phase 8 Shadow-venue with V16 gauntlet blend (Batch 5D)
- `reports/gauntlet_weight_sweep.md` — Phase 8 Walk-forward weight optimization (Batch 5D)
- `reports/shadow_sensitivity.md` — Phase 8 Slippage/latency/partial-fill sensitivity (Batch 6C)
- `reports/rolling_calibration.md` — Phase 7 Per-epoch rolling calibration drift (Batch 7C)
- `reports/gauntlet_weight_sweep_full.md` — Phase 8 Full-sample V16 weight sweep (Batch 8A)
- `reports/hard_gate_sweep.md` — Phase 9 Hard-gate threshold sweep (Batch 9B)
- `reports/hard_gate_attribution.md` — Phase 9 Hard-gate day attribution (Batch 9B)
- `reports/gate_pnl_attribution.md` — Phase 10 Per-gate PnL attribution + outcome weights (Batch 10A/10B)
- `reports/ow_validation.md` — Phase 11 OOS validation of outcome weights (Batch 11A)
- `reports/calibration.md` — Phase 3 ml_scorer calibration
- `reports/bayesian_expectancy.md` — Phase 5 posteriors + heat
- `reports/firm_reviews/<variant>.md` — Phase 3 markdown Firm memo
- `reports/firm_reviews/<variant>_live.md` — Phase 3 LIVE Firm verdict
- `reports/post_mortems/*.md` — Phase 3 per-trade post-mortems
- `reports/daily/YYYY-MM-DD.md` — Phase 1 end-of-session digest
- `reports/firm_integration.md` — Phase 0 Firm bridge probe
- `reports/alerting.md` · `reports/edge_decay.md` · `reports/mae_mfe.md` · `reports/time_heatmap.md` — Phase A
- `reports/counterfactual.md` · `reports/trade_governor.md` · `reports/rule_adherence.md` · `reports/email_recap.md` · `reports/time_exit.md` — Phase A
- `reports/psych_sidecar.md` · `reports/pre_trade_pause.md` · `reports/loss_streak.md` · `reports/hot_hand.md` — Phase B
- `reports/auto_screenshot.md` · `reports/voice_memos.md` · `reports/weekly_review.md` · `reports/monthly_narrative.md` · `reports/mistake_taxonomy.md` · `reports/ai_reviewer.md` — Phase B
- `reports/cumulative_delta.md` · `reports/volume_profile.md` · `reports/sector_rotation.md` · `reports/news_feed.md` · `reports/gex_monitor.md` — Phase C
- `reports/vix_term.md` · `reports/breadth.md` · `reports/event_calendar.md` · `reports/earnings_amp.md` · `reports/seasonality.md` — Phase C
- `reports/gbm_filter.md` · `reports/shap_rank.md` · `reports/trade_clusters.md` · `reports/anomaly.md` · `reports/heartbeat.md` — Phase D
- `reports/deadman_switch.md` · `reports/encrypted_backup.md` · `reports/tax_1256.md` · `reports/pretrade_checklist.md` · `reports/correlation_cap.md` — Phase D
- `reports/gate_chain.md` · `reports/parity.md` · `reports/gauntlet.md` — Phase E (enforcement spine)
- `reports/burn_in.md` · `reports/eta_v3.md` · `reports/eta_v3_probe.md` · `reports/eta_v3_enrich.md` · `reports/eta_v3_meta.md` — Phase F (closing reporters; `eta_v3_bridge.md` retained as legacy alias)
- `reports/backtest_real.md` · `reports/backtest_real_analysis.md` · `reports/backtest_real_trades.csv` — Phase 12 (real-tape backtest; `data/backtest_real_daily.json` for gate revalidation)
