# Apex V3 AgentInput Enrichment — 2026-04-17T01:39:42.994982+00:00

**Engine available:** 🟢 yes
**Snapshot source:** eta_v3 firm_engine.evaluate
**AgentInput source:** stub AgentInput (bridge not wired)

## Voice summary

```
eta_v3: HOLD LONG · regime=NEUTRAL · pm_final=+15.3 · quant=+15.3 · red=0.0 · agree=4/15 · setup=—
```

## AgentInput.payload keys

| Before enrichment | After enrichment |
|---|---|
| `['price', 'qty', 'side', 'symbol', 'trace_id']` | `['eta_v3_direction', 'eta_v3_pm_final', 'eta_v3_regime', 'eta_v3_voices', 'price', 'qty', 'side', 'symbol', 'trace_id']` |

**Added keys:** `eta_v3_direction`, `eta_v3_pm_final`, `eta_v3_regime`, `eta_v3_voices`

## Added payload content

```json
{
  "eta_v3_direction": 1,
  "eta_v3_pm_final": 15.27,
  "eta_v3_regime": "NEUTRAL",
  "eta_v3_voices": {
    "blocked_reason": "pm_below_threshold (15.3 < 40.0)",
    "direction": 1,
    "fire_long": false,
    "fire_short": false,
    "pm_final": 15.27,
    "quant_total": 15.27,
    "red_team": 0.0,
    "red_team_weighted": 0.0,
    "regime": "NEUTRAL",
    "setup_name": "",
    "source": "eta_v3",
    "voice_agree": 4,
    "voices": {
      "v1": 0.0,
      "v10": 0.0,
      "v11": 0.0,
      "v12": 66.7,
      "v13": -30.0,
      "v14": 0.0,
      "v15": 0.0,
      "v2": 15.0,
      "v3": 0.0,
      "v4": 15.0,
      "v5": 0.0,
      "v6": 80.0,
      "v7": 0.0,
      "v8": 0.0,
      "v9": 0.0
    }
  }
}
```

This closes the loop from the adapter into the Firm review
contract: the Quant agent inside the 6-stage chain sees
``payload['eta_v3_voices']`` alongside the base spec fields.
The enrichment is idempotent — calling again with the same
snapshot produces the same dict (no duplicate keys).
