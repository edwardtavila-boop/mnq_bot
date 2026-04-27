# Firm Review (LIVE) — `r5_real_wide_target`

This review was produced by the real six-stage Firm Python
agents, invoked through `mnq.firm_runtime.run_six_stage_review`.

**Apex V3 enrichment:** active — payload carries
`eta_v3_voices` for QuantAgent consumption.

```
eta_v3: HOLD LONG · regime=NEUTRAL · pm_final=+10.1 · quant=+10.1 · red=0.0 · agree=4/15 · setup=—
```

## Strategy spec fed to the Firm

```json
{
  "strategy_id": "r5_real_wide_target",
  "sample_size": 1,
  "expected_expectancy_r": -1.05,
  "oos_degradation_pct": 100.0,
  "entry_logic": "EMA9/EMA21 cross, min spread 2.00 pts, vol filter \u03c3\u226417.0, hard pause \u03c3>28.0, orderflow proxy\u22650.60",
  "stop_logic": "40-tick hard stop; time stop 20 bars",
  "target_logic": "2.0R fixed target",
  "dd_kill_switch_r": 12.0,
  "regimes_approved": [],
  "approved_sessions": [
    "RTH"
  ]
}
```

## Stage verdicts

| Stage | Verdict | P(ok) | 95% CI | Horizon |
|---|---|---:|---|---|
| `quant` | **MODIFY** | 0.07 | 0.00 / 0.22 | strategy_lifetime |
| `red_team` | **KILL** | 0.95 | 0.75 / 1.00 | first_100_trades |
| `risk` | **HOLD** | 0.70 | 0.55 / 0.85 | strategy_lifetime |
| `macro` | **MODIFY** | 0.55 | 0.40 / 0.70 | session |
| `micro` | **MODIFY** | 0.65 | 0.50 / 0.80 | immediate |
| `pm` | **KILL** | 0.77 | 0.67 / 0.87 | strategy_lifetime |

## QUANT

- Reasoning: Spec rejected. Violations: oos_overfit: OOS degradation 100.0% strongly suggests parameter overfit.; Sample size 1 < 100 minimum; Expectancy -1.050R <= 0; OOS degradation 100.0% > 50% threshold Apex V3 corroboration: agree=4/15, dir=LONG, regime=NEUTRAL, fire=HOLD; delta_prob=-0.067.
- Primary driver: Sample size: 1, expectancy: -1.050R
- Secondary driver: OOS degradation: 100.0%
- Falsification: Live expectancy < -0.525R across first 50 trades, OR live OOS degradation exceeds 120.0%

```json
{
  "violations": [
    "oos_overfit: OOS degradation 100.0% strongly suggests parameter overfit.",
    "Sample size 1 < 100 minimum",
    "Expectancy -1.050R <= 0",
    "OOS degradation 100.0% > 50% threshold"
  ],
  "warnings": [
    "win_rate_ci: Win rate 50.0% has 95% CI lower bound 5.5% < 45%.",
    "Subagent dossier recommends HOLD."
  ],
  "eta_v3": {
    "consumed": true,
    "voice_agree": 4,
    "direction": 1,
    "direction_label": "LONG",
    "regime": "NEUTRAL",
    "fire_long": false,
    "fire_short": false,
    "fire_label": "HOLD",
    "blocked_reason": "pm_below_threshold (10.1 < 40.0)",
    "base_probability": 0.1355,
    "adjusted_probability": 0.06829166666666667,
    "delta": -0.06720833333333334,
    "blend_weight": 0.25,
    "penalty_applied": 0.1,
    "supporting": false,
    "strong_corroboration": false
  },
  "subagent_dossier": {
    "context": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target",
    "recommendation": "HOLD",
    "pressure_score": 0.43,
    "blocking_count": 1,
    "summary_by_subagent": {
      "statistics": 2
    },
    "summary_by_severity": {
      "info": 0,
      "warning": 1,
      "alert": 0,
      "veto": 1
    },
    "dominant_surfaces": [
      "oos_overfit",
      "win_rate_ci"
    ],
    "narrative": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci",
    "findings": [
      {
        "subagent": "statistics",
        "severity": "warning",
        "surface": "win_rate_ci",
        "claim": "Win rate 50.0% has 95% CI lower bound 5.5% < 45%.",
        "test": "Increase sample size or tighten entry filters.",
        "magnitude": 0.054619065145883494,
        "direction": ""
      },
      {
        "subagent": "statistics",
        "severity": "veto",
        "surface": "oos_overfit",
        "claim": "OOS degradation 100.0% strongly suggests parameter overfit.",
        "test": "Re-run with walk-forward optimization. Accept max 30% degradation.",
        "magnitude": 100.0,
        "direction": ""
      }
    ]
  }
}
```

## RED_TEAM

- Reasoning: 6 attack(s) filed (5 critical). Top attacks: overfitting | sample_size | regime_fragility Subagent dossier: Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci.
- Primary driver: overfitting
- Secondary driver: sample_size
- Falsification: If first 100 live trades show no instances of attacked failure modes, Red Team's confidence in attacks falls.

```json
{
  "attacks": [
    {
      "surface": "overfitting",
      "severity": "critical",
      "claim": "OOS degradation of 100.0% suggests parameter curve-fit. Sensitivity to \u00b115% parameter shifts not demonstrated.",
      "test": "Run sensitivity analysis on every tuned parameter. Edge must survive \u00b115% shifts."
    },
    {
      "surface": "sample_size",
      "severity": "critical",
      "claim": "N=1 produces wide confidence intervals on Sharpe and expectancy. Bootstrap CI lower bound likely below practical threshold.",
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
    },
    {
      "surface": "subagent_statistics_win_rate_ci",
      "severity": "survivable",
      "claim": "Win rate 50.0% has 95% CI lower bound 5.5% < 45%.",
      "test": "Increase sample size or tighten entry filters."
    },
    {
      "surface": "subagent_statistics_oos_overfit",
      "severity": "critical",
      "claim": "OOS degradation 100.0% strongly suggests parameter overfit.",
      "test": "Re-run with walk-forward optimization. Accept max 30% degradation."
    }
  ],
  "critical_count": 5,
  "subagent_dossier": {
    "context": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target",
    "recommendation": "HOLD",
    "pressure_score": 0.43,
    "blocking_count": 1,
    "summary_by_subagent": {
      "statistics": 2
    },
    "summary_by_severity": {
      "info": 0,
      "warning": 1,
      "alert": 0,
      "veto": 1
    },
    "dominant_surfaces": [
      "oos_overfit",
      "win_rate_ci"
    ],
    "narrative": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci",
    "findings": [
      {
        "subagent": "statistics",
        "severity": "warning",
        "surface": "win_rate_ci",
        "claim": "Win rate 50.0% has 95% CI lower bound 5.5% < 45%.",
        "test": "Increase sample size or tighten entry filters.",
        "magnitude": 0.054619065145883494,
        "direction": ""
      },
      {
        "subagent": "statistics",
        "severity": "veto",
        "surface": "oos_overfit",
        "claim": "OOS degradation 100.0% strongly suggests parameter overfit.",
        "test": "Re-run with walk-forward optimization. Accept max 30% degradation.",
        "magnitude": 100.0,
        "direction": ""
      }
    ]
  }
}
```

## RISK

- Reasoning: Kelly 0.000 too small to overcome costs. Reject for now. Subagent dossier: Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci.
- Primary driver: Kelly: 0.000, approved: 0.000
- Secondary driver: Critical RT attacks: 0
- Falsification: If realized DD exceeds 12.0R OR if live edge falls below 50% of backtest after 50 trades, sizing must be reduced or strategy killed.

```json
{
  "full_kelly": 0,
  "approved_kelly": 0.0,
  "approved_fraction_of_kelly": 0.18051875,
  "per_trade_risk_pct": 0.001,
  "daily_limit_r": -2.0,
  "weekly_limit_r": -5.0,
  "dd_kill_r": -12.0,
  "confluence_multiplier": 1.0,
  "reduction_vs_quant": 0.0015,
  "subagent_dossier": {
    "context": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target",
    "recommendation": "HOLD",
    "pressure_score": 0.43,
    "blocking_count": 1,
    "summary_by_subagent": {
      "statistics": 2
    },
    "summary_by_severity": {
      "info": 0,
      "warning": 1,
      "alert": 0,
      "veto": 1
    },
    "dominant_surfaces": [
      "oos_overfit",
      "win_rate_ci"
    ],
    "narrative": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci",
    "findings": [
      {
        "subagent": "statistics",
        "severity": "warning",
        "surface": "win_rate_ci",
        "claim": "Win rate 50.0% has 95% CI lower bound 5.5% < 45%.",
        "test": "Increase sample size or tighten entry filters.",
        "magnitude": 0.054619065145883494,
        "direction": ""
      },
      {
        "subagent": "statistics",
        "severity": "veto",
        "surface": "oos_overfit",
        "claim": "OOS degradation 100.0% strongly suggests parameter overfit.",
        "test": "Re-run with walk-forward optimization. Accept max 30% degradation.",
        "magnitude": 100.0,
        "direction": ""
      }
    ]
  }
}
```

## MACRO

- Reasoning: Regime 'unknown' matches but confluence flags: statistics:oos_overfit - OOS degradation 100.0% strongly suggests parameter overfit. Subagent dossier: Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci.
- Primary driver: Regime: unknown, transition: False
- Secondary driver: Match: False, approved: []
- Falsification: Regime classification revised within 24h, OR major catalyst surprise. Re-evaluate all open positions.

```json
{
  "current_regime": "unknown",
  "is_transition": false,
  "regime_match": false,
  "major_catalysts": [],
  "recommendation": "MODIFY",
  "subagent_dossier": {
    "context": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target",
    "recommendation": "HOLD",
    "pressure_score": 0.43,
    "blocking_count": 1,
    "summary_by_subagent": {
      "statistics": 2
    },
    "summary_by_severity": {
      "info": 0,
      "warning": 1,
      "alert": 0,
      "veto": 1
    },
    "dominant_surfaces": [
      "oos_overfit",
      "win_rate_ci"
    ],
    "narrative": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci",
    "findings": [
      {
        "subagent": "statistics",
        "severity": "warning",
        "surface": "win_rate_ci",
        "claim": "Win rate 50.0% has 95% CI lower bound 5.5% < 45%.",
        "test": "Increase sample size or tighten entry filters.",
        "magnitude": 0.054619065145883494,
        "direction": ""
      },
      {
        "subagent": "statistics",
        "severity": "veto",
        "surface": "oos_overfit",
        "claim": "OOS degradation 100.0% strongly suggests parameter overfit.",
        "test": "Re-run with walk-forward optimization. Accept max 30% degradation.",
        "magnitude": 100.0,
        "direction": ""
      }
    ]
  }
}
```

## MICRO

- Reasoning: EXECUTABLE: spread 1.0t, latency 200ms, edge cost 0% Subagent dossier: Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci.
- Primary driver: Spread: 1.0t, edge cost: 0%
- Secondary driver: Liquidity ratio: 1.00
- Falsification: Realized slippage > 1.5 ticks on next 5 fills OR realized latency > 500ms

```json
{
  "current_spread_ticks": 1.0,
  "liquidity_ratio": 1.0,
  "latency_ms": 200,
  "edge_cost_pct": 0.0,
  "verdict": "MODIFY",
  "violations": [],
  "subagent_dossier": {
    "context": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target",
    "recommendation": "HOLD",
    "pressure_score": 0.43,
    "blocking_count": 1,
    "summary_by_subagent": {
      "statistics": 2
    },
    "summary_by_severity": {
      "info": 0,
      "warning": 1,
      "alert": 0,
      "veto": 1
    },
    "dominant_surfaces": [
      "oos_overfit",
      "win_rate_ci"
    ],
    "narrative": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci",
    "findings": [
      {
        "subagent": "statistics",
        "severity": "warning",
        "surface": "win_rate_ci",
        "claim": "Win rate 50.0% has 95% CI lower bound 5.5% < 45%.",
        "test": "Increase sample size or tighten entry filters.",
        "magnitude": 0.054619065145883494,
        "direction": ""
      },
      {
        "subagent": "statistics",
        "severity": "veto",
        "surface": "oos_overfit",
        "claim": "OOS degradation 100.0% strongly suggests parameter overfit.",
        "test": "Re-run with walk-forward optimization. Accept max 30% degradation.",
        "magnitude": 100.0,
        "direction": ""
      }
    ]
  }
}
```

## PM

- Reasoning: KILL: ['red_team'] vetoed. Cannot ship. Subagent dossier: Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci. Apex V3 PM corroboration: agree=4/15, pm_final=+10.1 (below gate), align=MATCH, delta_prob=-0.127.
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
    "macro": "MODIFY",
    "micro": "MODIFY"
  },
  "eta_v3": {
    "consumed": true,
    "voice_agree": 4,
    "direction": 1,
    "direction_label": "LONG",
    "regime": "NEUTRAL",
    "pm_final": 10.09,
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
  },
  "subagent_dossier": {
    "context": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target",
    "recommendation": "HOLD",
    "pressure_score": 0.43,
    "blocking_count": 1,
    "summary_by_subagent": {
      "statistics": 2
    },
    "summary_by_severity": {
      "info": 0,
      "warning": 1,
      "alert": 0,
      "veto": 1
    },
    "dominant_surfaces": [
      "oos_overfit",
      "win_rate_ci"
    ],
    "narrative": "Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci",
    "findings": [
      {
        "subagent": "statistics",
        "severity": "warning",
        "surface": "win_rate_ci",
        "claim": "Win rate 50.0% has 95% CI lower bound 5.5% < 45%.",
        "test": "Increase sample size or tighten entry filters.",
        "magnitude": 0.054619065145883494,
        "direction": ""
      },
      {
        "subagent": "statistics",
        "severity": "veto",
        "surface": "oos_overfit",
        "claim": "OOS degradation 100.0% strongly suggests parameter overfit.",
        "test": "Re-run with walk-forward optimization. Accept max 30% degradation.",
        "magnitude": 100.0,
        "direction": ""
      }
    ]
  }
}
```

## Final verdict (PM)

**KILL** — KILL: ['red_team'] vetoed. Cannot ship. Subagent dossier: Candidate strategy `r5_real_wide_target`: n=1, E=-1.050R:r5_real_wide_target: 2 finding(s); warning=1, veto=1; pressure=0.430; top surfaces=oos_overfit, win_rate_ci. Apex V3 PM corroboration: agree=4/15, pm_final=+10.1 (below gate), align=MATCH, delta_prob=-0.127.
