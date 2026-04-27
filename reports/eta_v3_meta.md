# Apex V3 Meta-Firm — 2026-04-26T14:26:27.577258+00:00

**Engine available:** yes
**Snapshot source:** eta_v3 firm_meta.run_meta_firm

- Meta-voices exposed: **8**
- `run_meta_firm` callable: **True**
- `MetaContext` dataclass: **True**

## Meta-voice names

```
mv_correlation_agreement
mv_day_of_week
mv_drawdown_check
mv_recent_performance
mv_regime_stability
mv_streak_detector
mv_time_of_day
mv_volatility_regime
```

## Single-line summary

```
eta_v3_meta: TRADE · regime=NEUTRAL · pm=28.3 · size_x=1.00 · budget=2.0R · setups=3/3 · conf=56
```

## Payload enrichment

Base payload:

```json
{
  "qty": 1,
  "side": "long",
  "symbol": "MNQ",
  "trace_id": "meta-smoke"
}
```

Enriched keys (`eta_v3_meta*`):

```json
{
  "eta_v3_meta": {
    "audit": {
      "pm_threshold": "base=30.0, conf=55.7 \u2192 PM=28.3",
      "regime_vote": "vol_regime=+10, stability=+0 \u2192 NEUTRAL"
    },
    "confidence": 55.7,
    "enabled_setups": [
      "ORB",
      "EMA PB",
      "SWEEP"
    ],
    "pm_threshold": 28.3,
    "reason": "TRADE: meta-confidence 56/100, 3 setups active",
    "regime_vote": "NEUTRAL",
    "risk_budget_R": 2.0,
    "size_multiplier": 1.0,
    "source": "eta_v3_meta",
    "trade_allowed": true,
    "voices": {
      "correlation_agreement": 0.0,
      "day_of_week": 50.0,
      "drawdown_check": 0.0,
      "recent_performance": 0.0,
      "regime_stability": 0.0,
      "streak_detector": 0.0,
      "time_of_day": 60.0,
      "volatility_regime": 10.0
    }
  },
  "eta_v3_meta_pm_threshold": 28.3,
  "eta_v3_meta_regime_vote": "NEUTRAL",
  "eta_v3_meta_risk_budget_R": 2.0,
  "eta_v3_meta_size_multiplier": 1.0,
  "eta_v3_meta_trade_allowed": true
}
```

## Strategy-param overrides

Base params:

```json
{
  "allowed_setups": [
    "ORB",
    "EMA PB",
    "SWEEP"
  ],
  "daily_loss_cap_r": 3.0,
  "pm_gate": 40.0,
  "size_multiplier": 1.0
}
```

Overridden params (changed vs base):

```json
{
  "daily_loss_cap_r": 2.0,
  "meta_confidence": 55.7,
  "meta_reason": "TRADE: meta-confidence 56/100, 3 setups active",
  "meta_regime_vote": "NEUTRAL",
  "pm_gate": 28.3,
  "trade_allowed": true
}
```

## Full MetaSnapshot

```json
{
  "audit": {
    "pm_threshold": "base=30.0, conf=55.7 \u2192 PM=28.3",
    "regime_vote": "vol_regime=+10, stability=+0 \u2192 NEUTRAL"
  },
  "confidence": 55.7,
  "enabled_setups": [
    "ORB",
    "EMA PB",
    "SWEEP"
  ],
  "pm_threshold": 28.3,
  "reason": "TRADE: meta-confidence 56/100, 3 setups active",
  "regime_vote": "NEUTRAL",
  "risk_budget_R": 2.0,
  "size_multiplier": 1.0,
  "source": "eta_v3_meta",
  "trade_allowed": true,
  "voices": {
    "correlation_agreement": 0.0,
    "day_of_week": 50.0,
    "drawdown_check": 0.0,
    "recent_performance": 0.0,
    "regime_stability": 0.0,
    "streak_detector": 0.0,
    "time_of_day": 60.0,
    "volatility_regime": 10.0
  }
}
```

This reporter is read-only. The overrides surface in
`scripts/firm_live_review.py` as an additional payload fragment
and, where the orchestrator honours them, a per-run override
of PM gate, size multiplier, and daily loss cap.
