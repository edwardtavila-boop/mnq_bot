# Firm Review (LIVE) — `r5_real_wide_target`

This review was produced by the real six-stage Firm Python
agents, invoked through `mnq.firm_runtime.run_six_stage_review`.

**Apex V3 enrichment:** active — payload carries
`eta_v3_voices` for QuantAgent consumption.

```
eta_v3: HOLD LONG · regime=NEUTRAL · pm_final=+15.4 · quant=+15.4 · red=0.0 · agree=4/15 · setup=—
```

## Strategy spec fed to the Firm

```json
{
  "strategy_id": "r5_real_wide_target",
  "sample_size": 8,
  "expected_expectancy_r": 0.071875,
  "oos_degradation_pct": 120.51282051282051,
  "entry_logic": "EMA9/EMA21 cross, min spread 2.00 pts, vol filter \u03c3\u226417.0, hard pause \u03c3>28.0, orderflow proxy\u22650.60",
  "stop_logic": "40-tick hard stop; time stop 20 bars",
  "target_logic": "2.0R fixed target",
  "dd_kill_switch_r": 12.0,
  "regimes_approved": [
    "real_trend_up"
  ],
  "approved_sessions": [
    "RTH"
  ]
}
```

## Stage verdicts

| Stage | Verdict | P(ok) | 95% CI | Horizon |
|---|---|---:|---|---|
| `quant` | **MODIFY** | 0.12 | 0.00 / 0.27 | strategy_lifetime |
| `red_team` | **KILL** | 0.90 | 0.70 / 1.00 | first_100_trades |
| `risk` | **HOLD** | 0.70 | 0.55 / 0.85 | strategy_lifetime |
| `macro` | **GO** | 0.50 | 0.35 / 0.65 | session |
| `micro` | **GO** | 0.60 | 0.45 / 0.75 | immediate |
| `pm` | **KILL** | 0.77 | 0.67 / 0.87 | strategy_lifetime |

## QUANT

- Reasoning: Spec rejected. Violations: Sample size 8 < 100 minimum; OOS degradation 120.51282051282051% > 50% threshold Apex V3 corroboration: agree=4/15, dir=LONG, regime=NEUTRAL, fire=HOLD; delta_prob=-0.083.
- Primary driver: Sample size: 8, expectancy: 0.072R
- Secondary driver: OOS degradation: 120.51282051282051%
- Falsification: Live expectancy < 0.036R across first 50 trades, OR live OOS degradation exceeds 140.5128205128205%

```json
{
  "violations": [
    "Sample size 8 < 100 minimum",
    "OOS degradation 120.51282051282051% > 50% threshold"
  ],
  "warnings": [],
  "eta_v3": {
    "consumed": true,
    "voice_agree": 4,
    "direction": 1,
    "direction_label": "LONG",
    "regime": "NEUTRAL",
    "fire_long": false,
    "fire_short": false,
    "fire_label": "HOLD",
    "blocked_reason": "pm_below_threshold (15.4 < 40.0)",
    "base_probability": 0.2,
    "adjusted_probability": 0.11666666666666667,
    "delta": -0.08333333333333334,
    "blend_weight": 0.25,
    "penalty_applied": 0.1,
    "supporting": false,
    "strong_corroboration": false
  }
}
```

## RED_TEAM

- Reasoning: 4 attack(s) filed (4 critical). Top attacks: overfitting | sample_size | regime_fragility
- Primary driver: overfitting
- Secondary driver: sample_size
- Falsification: If first 100 live trades show no instances of attacked failure modes, Red Team's confidence in attacks falls.

```json
{
  "attacks": [
    {
      "surface": "overfitting",
      "severity": "critical",
      "claim": "OOS degradation of 120.51282051282051% suggests parameter curve-fit. Sensitivity to \u00b115% parameter shifts not demonstrated.",
      "test": "Run sensitivity analysis on every tuned parameter. Edge must survive \u00b115% shifts."
    },
    {
      "surface": "sample_size",
      "severity": "critical",
      "claim": "N=8 produces wide confidence intervals on Sharpe and expectancy. Bootstrap CI lower bound likely below practical threshold.",
      "test": "Bootstrap 10,000 samples, report 95% CI on expectancy. If lower bound < 0.20R, reject."
    },
    {
      "surface": "regime_fragility",
      "severity": "critical",
      "claim": "Strategy approved for only 0 regime(s). Backtest sample regime breakdown not validated.",
      "test": "Run on held-out regime periods (2020 COVID, Q4 2018, 2022 bear). Report per-regime expectancy."
    },
    {
      "surface": "execution",
      "severity": "critical",
      "claim": "Slippage assumption not stated. MNQ entries during volatile minutes can slip 1-3 ticks. At avg 6-pt stop, 2-tick slippage is 8% of stop distance.",
      "test": "Re-run backtest with regime-adjusted slippage: 0.5 ticks normal, 1.5 ticks elevated, 3 ticks crisis."
    }
  ],
  "critical_count": 4
}
```

## RISK

- Reasoning: Kelly 0.000 too small to overcome costs. Reject for now.
- Primary driver: Kelly: 0.000, approved: 0.000
- Secondary driver: Critical RT attacks: 0
- Falsification: If realized DD exceeds 12.0R OR if live edge falls below 50% of backtest after 50 trades, sizing must be reduced or strategy killed.

```json
{
  "full_kelly": 0,
  "approved_kelly": 0.0,
  "approved_fraction_of_kelly": 0.25,
  "per_trade_risk_pct": 0.001,
  "daily_limit_r": -2.0,
  "weekly_limit_r": -5.0,
  "dd_kill_r": -12.0,
  "reduction_vs_quant": 0.0015
}
```

## MACRO

- Reasoning: Regime 'unknown' matches approved. No major catalysts pending.
- Primary driver: Regime: unknown, transition: False
- Secondary driver: Match: False, approved: []
- Falsification: Regime classification revised within 24h, OR major catalyst surprise. Re-evaluate all open positions.

```json
{
  "current_regime": "unknown",
  "is_transition": false,
  "regime_match": false,
  "major_catalysts": [],
  "recommendation": "GO"
}
```

## MICRO

- Reasoning: EXECUTABLE: spread 1.0t, latency 200ms, edge cost 0%
- Primary driver: Spread: 1.0t, edge cost: 0%
- Secondary driver: Liquidity ratio: 1.00
- Falsification: Realized slippage > 1.5 ticks on next 5 fills OR realized latency > 500ms

```json
{
  "current_spread_ticks": 1.0,
  "liquidity_ratio": 1.0,
  "latency_ms": 200,
  "edge_cost_pct": 0.0,
  "verdict": "GO",
  "violations": []
}
```

## PM

- Reasoning: KILL: ['red_team'] vetoed. Cannot ship. Apex V3 PM corroboration: agree=4/15, pm_final=+15.4 (below gate), align=MATCH, delta_prob=-0.127.
- Primary driver: vetoes from ['red_team']
- Falsification: Strategy revised to address all KILL verdicts

```json
{
  "killing_agents": [
    "red_team"
  ],
  "all_verdicts": {
    "quant": "MODIFY",
    "red_team": "KILL",
    "risk": "HOLD",
    "macro": "GO",
    "micro": "GO"
  },
  "eta_v3": {
    "consumed": true,
    "voice_agree": 4,
    "direction": 1,
    "direction_label": "LONG",
    "regime": "NEUTRAL",
    "pm_final": 15.38,
    "engine_live": false,
    "engine_gate": 40.0,
    "strong_corroboration": false,
    "verdict_alignment": 1,
    "verdict_alignment_label": "MATCH",
    "base_probability": 0.9,
    "adjusted_probability": 0.7733333333333334,
    "delta": -0.1266666666666666,
    "blend_weight": 0.2,
    "bonus_applied": 0.0,
    "penalty_applied": 0.0
  }
}
```

## Final verdict (PM)

**KILL** — KILL: ['red_team'] vetoed. Cannot ship. Apex V3 PM corroboration: agree=4/15, pm_final=+15.4 (below gate), align=MATCH, delta_prob=-0.127.
