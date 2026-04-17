# Calibration Layer (Tier 2+)

Every agent output carries four quantified fields: **probability** (0–100%), **confidence interval**, **time horizon**, and **falsification criteria**. Without all four, output is rejected.

## Three scoring methods, used in parallel

**Brier score** — `(probability - outcome)²`, averaged. Lower is better (0 = perfect, 0.25 = coin flip). Use for binary calls.

**Log loss** — `-(y·log(p) + (1-y)·log(1-p))`. Punishes confident wrong calls exponentially harder than Brier. Use when overconfidence is the failure mode you most fear — which, in trading, it usually is.

**Calibration plot** — bucket predictions by stated confidence (50-60%, 60-70%, etc.) and check if actual hit rates match. An agent saying 80% should be right 80% of the time. Most aren't. The shape reveals systematic miscalibration.

## The quadrant classification

Post-Mortem classifies every resolved call:

|  | Outcome matched | Outcome didn't match |
|---|---|---|
| **Reasoning matched driver** | LEGIT WIN | UNLUCKY (fine) |
| **Reasoning didn't match** | LUCKY (DANGER) | WRONG (teaches) |

**Lucky wins are the trap.** Agent predicts ES up due to "earnings" and ES rallies on "short covering." The call is right, the reasoning is wrong. If you weight the agent up based on outcome, you train the Firm to chase noise. **Reward reasoning quality, not outcome.**

## Weighting

Once an agent has 50+ resolved predictions in a given context, weight their influence by calibration:

```
base_weight = 1.0
brier_adj = (0.25 - brier) × 2
cal_adj = -mean_abs_calibration_gap × 2
luck_penalty = -lucky_ratio × 1.5
final_weight = max(0.1, base + brier_adj + cal_adj + luck_penalty)
```

Recalculate monthly. Apply to agent dissent strength in future decisions.

## Sealed submissions

Agents must not see each other's predictions before committing. Independence is the whole point. Write to a sealed buffer. Reveal simultaneously. No exceptions.

## Ledger schema per call

```
call_id, timestamp, agent, strategy_id, regime_at_call,
prediction: {probability, CI, time_horizon, falsification},
reasoning: {primary_driver, secondary, tertiary},
outcome: {resolved_ts, actual_result, actual_driver, thesis_match, quadrant},
scores: {brier, log_loss, calibration_bucket, running_weight}
```

Every call. Every agent. Every time. This is the data that makes all other tiers work.
