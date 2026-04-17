# Apex V3 Bridge — 2026-04-17T01:39:43.047974+00:00

**Engine available:** 🟢 yes
**Voices exposed:** 15 · evaluate=True · detect_regime=True

## Voice snapshot

```
eta_v3: HOLD LONG · regime=NEUTRAL · pm_final=+15.3 · quant=+15.3 · red=0.0 · agree=4/15 · setup=—
```

## Payload enrichment

Base payload:

```json
{
  "qty": 1,
  "side": "long",
  "symbol": "MNQ",
  "trace_id": "bridge-smoke"
}
```

Enriched payload (keys added):

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

## Stub path (engine absent)

`build_enrichment_payload(base, None)` returns the base unchanged — proves the adapter's fail-open contract. Stub === base: True.

This reporter is read-only. The adapter is consumed by the
Quant agent inside the existing Firm bridge shim at
`src/mnq/firm_runtime.py` — no new import boundaries introduced.
