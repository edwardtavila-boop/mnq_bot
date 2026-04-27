# EVOLUTIONARY TRADING ALGO Inventory and Mission Roadmap

Date: 2026-04-21

## Scope Note

I could not find a separate `btcbot` repository or any `Alfred` references in the current workspace.
The working tree here is `mnq_bot`, which is the current EVOLUTIONARY TRADING ALGO codebase, plus the legacy
`eta_v3_framework/` subframework that is still adapted into the main system through `src/mnq/eta_v3/`.

## What Is Verified Today

- The current unit test suite passes: `1275 passed, 2 skipped`.
- The Firm bridge is live and ready for use:
  - `scripts/firm_bridge.py`
  - `src/mnq/firm_runtime.py`
  - `reports/firm_integration.json`
- Apex V3 integration is present as a fail-open adapter layer:
  - `src/mnq/eta_v3/adapter.py`
  - `src/mnq/eta_v3/meta_adapter.py`
  - `scripts/eta_v3_probe.py`
  - `scripts/eta_v3_bridge.py`
  - `scripts/eta_v3_meta.py`
  - `scripts/eta_meta_orchestrator.py`
- The promotion/risk spine is implemented:
  - `src/mnq/risk/tiered_rollout.py`
  - `src/mnq/risk/rollout_store.py`
  - `src/mnq/gauntlet/ship_manifest.py`
  - `scripts/promotion_pipeline.py`
  - `scripts/tier_driver.py`
- New signal and risk plumbing is in place:
  - `src/mnq/features/microstructure.py`
  - `src/mnq/gauntlet/orderflow.py`
  - `src/mnq/gauntlet/outcome_weights.py`
  - `src/mnq/observability/tolerance_harness.py`
  - `src/mnq/observability/metrics.py`
- The latest edge-forensics artifacts say the only live-grade family is the ORB cluster:
  - `orb_only_pm30`
  - `orb_regime_pm30`
  - `orb_sweep_pm30`
  - `orb_regime_conf_pm25`
  - `orb_confidence_pm30`
  - `orb_regime_conf_pm30`

## What Is Still External

- Phase 6: VPS / deployment / backup / alerting
- Phase 7: Real broker integration
- Phase 8: Shadow trading and live quote feed
- Phase 9: Tiered live rollout

Those are already documented in `reports/external_infra_milestones.md` and remain blocked on outside infrastructure, not just code.

## Current State Summary

### Real and working

- Core runtime, types, features, executor, risk gates, and bridge layers are implemented.
- The APEX V3 and meta-firm paths are wired as observation/enrichment adapters.
- Promotion gating is deterministic and backed by persisted rollout state.
- The full test suite is green.

### Report-only or observational

- `scripts/eta_v3_probe.py`
- `scripts/eta_v3_bridge.py`
- `scripts/eta_v3_meta.py`
- `scripts/eta_meta_orchestrator.py`

These are useful diagnostics, but they are intentionally fail-open and do not themselves enforce trading decisions.

### Stale or inconsistent artifacts

- `reports/run_all_phases.md` still shows an older run with many stage failures.
- That conflicts with the current passing unit suite and should be treated as a historical snapshot, not the current truth.

## Mission-Fit Roadmap

### 1. Canonize the live edge

- Treat the ORB family as the only shippable strategy set.
- Retire or isolate the killed variants so they do not pollute promotion workflows.
- Keep `reports/edge_forensics.json`, `reports/promotion_manifest.json`, and `reports/strategy_registry.json` aligned.

### 2. Make the bridge surface Jarvis-grade

- Keep `src/mnq/firm_runtime.py` as the only import boundary to the external Firm package.
- Ensure the payload shape from `src/mnq/eta_v3` stays stable.
- Add/keep tests around fail-open behavior and the exact enrichment keys used by the Quant and PM stages.

### 3. Close the last internal wiring gaps

- Keep `scripts/run_all_phases.py` aligned with actual script exit behavior.
- Remove or repair any stale reporter expectations that still produce false failures.
- Continue to keep the scorecard and burn-in artifacts UTF-8 clean on Windows.

### 4. Build the external execution stack

- Provision VPS.
- Wire deployment, restart, backups, heartbeat, and alerting.
- Connect the broker and live market data feed.
- Stand up shadow trading before any live route.

### 5. Promote only through tiered live

- Start at Tier 0 paper only.
- Move to Tier 1 only after shadow and gating are clean.
- Keep the manual approval and kill-switch rules non-negotiable.

## Recommended Next Work

1. Clean up the stale `run_all_phases` expectations so the orchestration snapshot matches the current codebase.
2. Keep ORB-family promotion as the only live candidate set.
3. Finish the external infrastructure milestones.
4. After that, wire shadow trading and broker execution before any live capital.

