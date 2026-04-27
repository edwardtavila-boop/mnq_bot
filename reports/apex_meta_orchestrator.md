# Apex V3 Meta-Orchestrator Config — 2026-04-26T14:26:27.709552+00:00

**Source:** live (firm_meta engine)
**Trade allowed:** YES
**Confidence:** 53.1%
**Regime vote:** NEUTRAL

## Summary

```eta_v3_meta: TRADE · regime=NEUTRAL · pm=39.1 · size_x=1.00 · budget=2.0R · setups=3/3 · conf=53```

## Runtime parameters

| Parameter | Default | Override | Changed |
|---|---:|---:|:---|
| allowed_setups | ['ORB', 'EMA PB', 'SWEEP'] | ['ORB', 'EMA PB', 'SWEEP'] |  |
| daily_loss_cap_r | 3.0 | 2.0 | **YES** |
| gauntlet_weight | 0.15 | 0.15 |  |
| max_trades_per_day | 5 | 5 |  |
| pm_gate | 40.0 | 39.1 | **YES** |
| size_multiplier | 1.0 | 1.0 |  |

## Meta-voice outputs

```json
{
  "correlation_agreement": 0.0,
  "day_of_week": 0.0,
  "drawdown_check": 0.0,
  "recent_performance": 0.0,
  "regime_stability": 0.0,
  "streak_detector": 0.0,
  "time_of_day": 60.0,
  "volatility_regime": 10.0
}
```

## Integration status

- Config written to: `data\meta_config.json`
- The orchestrator reads `data/meta_config.json` at startup
  and applies the overridden parameters to the current run.
- When `trade_allowed=false`, the orchestrator should skip
  all live trade execution stages (shadow mode only).

## How to use

```python
config = json.loads(Path("data/meta_config.json").read_text())
if not config["trade_allowed"]:
    print("META-FIRM: trading paused today")
    # Skip execution, run shadow only
```
