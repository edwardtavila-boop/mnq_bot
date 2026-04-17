# External Infrastructure Milestones

Generated: 2026-04-16

Phases 6–9 require external infrastructure that can't be automated in-repo.
This document tracks each milestone, its dependencies, and target dates.

## Phase 6 — API Boundary / VPS

| # | Milestone | Dependency | Target | Status |
|---:|---|---|---|---|
| 6.1 | Provision VPS (Linux, ≥4GB RAM, <10ms to CME) | Account at Hetzner/OVH/Vultr | Week 1 | **PENDING** |
| 6.2 | Deploy mnq_bot via Docker/Podman | 6.1 + Dockerfile | Week 1 | **PENDING** |
| 6.3 | Set up systemd service + auto-restart | 6.2 | Week 1 | **PENDING** |
| 6.4 | Encrypted backup cron (GPG → S3/B2) | 6.2 | Week 1 | **PENDING** |
| 6.5 | Heartbeat/deadman switch → alerting (PagerDuty/Pushover) | 6.3 | Week 2 | **PENDING** |
| 6.6 | Daily digest email (cron → scripts/daily_digest.py) | 6.3 | Week 2 | **PENDING** |

**Pre-requisites already done in-repo:**
- `scripts/heartbeat.py` and `scripts/deadman_switch.py` — scaffolded
- `scripts/encrypted_backup.py` — GPG wrapper ready
- `scripts/daily_digest.py` — report generator ready

## Phase 7 — Real Broker

| # | Milestone | Dependency | Target | Status |
|---:|---|---|---|---|
| 7.1 | Tradovate account with API access | Broker application | Week 2 | **PENDING** |
| 7.2 | API key provisioning (read-only first) | 7.1 | Week 2 | **PENDING** |
| 7.3 | Wire Tradovate REST/WS client into `src/mnq/venues/tradovate/` | 7.2 | Week 3 | **PENDING** |
| 7.4 | Historical data backfill validation (compare vs Databento) | 7.3 | Week 3 | **PENDING** |
| 7.5 | Account info + position read-only tests | 7.3 | Week 3 | **PENDING** |
| 7.6 | Paper trading mode validation (SIM account) | 7.5 | Week 4 | **PENDING** |

**Pre-requisites already done in-repo:**
- `src/mnq/venues/tradovate/` — venue interface scaffolded
- `src/mnq/core/types.py` — Order/Fill/Signal types ready
- Shadow venue parity verified at ±$1.74

## Phase 8 — Shadow Trading

| # | Milestone | Dependency | Target | Status |
|---:|---|---|---|---|
| 8.1 | Live MNQ 1m quote feed (Tradovate WS or Databento live) | 7.3 | Week 4 | **PENDING** |
| 8.2 | Real-time bar aggregation (1m from tick stream) | 8.1 | Week 4 | **PENDING** |
| 8.3 | Shadow mode: run full pipeline on live quotes, journal decisions, NO execution | 8.2 | Week 5 | **PENDING** |
| 8.4 | 72h shadow burn-in — compare shadow decisions vs what would have happened | 8.3 | Week 5-6 | **PENDING** |
| 8.5 | Shadow parity report (live shadow vs historical backtest) | 8.4 | Week 6 | **PENDING** |
| 8.6 | OrderFlowTracker live mode (Bookmap WS or Tradovate DOM) | 8.1 | Week 5 | **PENDING** |
| 8.7 | ES correlation feed (live ES quotes for gate_correlation) | 8.1 | Week 5 | **PENDING** |

**Pre-requisites already done in-repo:**
- `src/mnq/gauntlet/orderflow.py` — OrderFlowTracker with live hooks
- `src/mnq/venues/shadow/` — full shadow venue with VolumeAwareSlippage
- `scripts/burn_in_72h.py` — burn-in framework scaffolded
- Gate correlation supports live ES closes

## Phase 9 — Tiered Live

| # | Milestone | Dependency | Target | Status |
|---:|---|---|---|---|
| 9.1 | Human gate: manual approval for first N live trades | 8.4 passes | Week 7 | **PENDING** |
| 9.2 | Tier 1: 1 contract, max 2 trades/day, $50 daily loss cap | 9.1 | Week 7 | **PENDING** |
| 9.3 | Tier 2: 1 contract, max 5 trades/day, $150 daily loss cap | 4 weeks of Tier 1 profitable | Week 11 | **PENDING** |
| 9.4 | Tier 3: 2 contracts, full strategy params | 4 weeks of Tier 2 profitable | Week 15 | **PENDING** |
| 9.5 | Kill switch: auto-flatten if daily loss exceeds cap | 9.2 | Week 7 | **PENDING** |
| 9.6 | Weekly review automation (scripts/weekly_review.py → Slack) | 9.2 | Week 8 | **PENDING** |

**Pre-requisites already done in-repo:**
- Trade governor with caps
- Loss streak monitor
- Deadman switch
- Weekly/monthly review generators
- Meta-firm trade_allowed kill switch

## Critical Path

```
[Week 1] VPS + Deploy
    ↓
[Week 2] Broker account + API keys
    ↓
[Week 3] Tradovate client integration
    ↓
[Week 4] Live quote feed + bar aggregation
    ↓
[Week 5-6] Shadow trading burn-in (72h minimum)
    ↓
[Week 7] Human-gated Tier 1 live (1 lot, 2 trades/day)
    ↓
[Week 11] Tier 2 promotion (if profitable)
    ↓
[Week 15] Tier 3 promotion (if profitable)
```

## Gating Criteria for Tier Promotion

- **Shadow → Tier 1**: 72h shadow burn-in with zero critical errors. Shadow PnL within 2σ of backtest expectancy.
- **Tier 1 → Tier 2**: 4 consecutive profitable weeks. Max drawdown < $200. No manual interventions needed.
- **Tier 2 → Tier 3**: 4 consecutive profitable weeks at Tier 2. Sharpe > 0.5 (annualized). Meta-firm confidence > 60%.
- **Any tier → Pause**: 3 consecutive losing days, daily loss > cap, or deadman switch trigger.
