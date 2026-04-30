# Run-All-Phases — 2026-04-27 19:22:28 UTC

- Stages: **2**
- Passed: **0/2**

## Per-phase summary

| Phase | Passed | Total |
|---|---:|---:|
| Phase 0 | 0 | 2 |

## Stage ledger

| # | Phase | Stage | Status | Duration (s) |
|---:|---|---|---|---:|
| 1 | Phase 0 | `firm_bridge` | FAIL (rc=2) | 0.1 |
| 2 | Phase 0 | `live_sim` | FAIL (rc=1) | 2.6 |

## Failures

### `firm_bridge` (Phase 0)

```
# stdout (last 10 lines)
## Gaps blocking integration

_none — contract satisfied._

## Next step

Continue running the markdown-only Firm review path (`scripts/firm_review.py`). Rerun this probe after each Firm-code fine-tune cycle; integration will auto-enable when the contract is met.

wrote C:\EvolutionaryTradingAlgo\mnq_bot\reports\firm_integration.md
wrote C:\EvolutionaryTradingAlgo\mnq_bot\reports\firm_integration.json
# stderr (last 10 lines)
NOT INTEGRATING: readiness probe failed.
```

### `live_sim` (Phase 0)

```
# stdout (last 10 lines)
<empty>
# stderr (last 10 lines)
Traceback (most recent call last):
  File "C:\EvolutionaryTradingAlgo\mnq_bot\scripts\live_sim.py", line 986, in <module>
    sys.exit(main())
             ~~~~^^
  File "C:\EvolutionaryTradingAlgo\mnq_bot\scripts\live_sim.py", line 910, in main
    raise ShimContractDriftError(probe_result.detail)
mnq._shim_probe.ShimContractDriftError: contract drift: locked=bf88d73c1a4a4e2f live=28c2a0f436fca531. Rerun `python scripts/firm_bridge.py --probe` to inspect, then `--integrate` to regenerate the shim.
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
