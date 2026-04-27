# Apex V3 Quant Fold-In — Batch 2.1

**Date:** 2026-04-16
**Scope:** Complete next-batch item #1 from `MANIFEST.md`: wire the 15-voice
Apex V3 firm_engine output into the Quant agent's probability estimate as a
corroboration signal.

## What landed

### `the_firm_complete/desktop_app/firm/agents/core.py`

`QuantAgent.evaluate()` now reads `payload["eta_v3_voices"]` when present
and folds the 15-voice agreement into its probability estimate. Four class
constants control the blend:

| Constant | Default | Purpose |
|---|---|---|
| `APEX_V3_BLEND_WEIGHT` | `0.25` | Weight of Apex signal in the probability blend |
| `APEX_V3_MIN_AGREE` | `8` | Voices out of 15 counted as "supporting" |
| `APEX_V3_STRONG_AGREE` | `12` | Voices out of 15 counted as "strong corroboration" |
| `APEX_V3_BLOCK_PENALTY` | `0.10` | Probability subtraction when Apex engine reports a block |
| `APEX_V3_DISAGREE_PENALTY` | `0.05` | Probability subtraction when Apex direction disagrees with spec side |

### Fold math

    apex_signal       = voice_agree / 15           # in [0, 1]
    blended           = (1 - w) · base + w · apex_signal
    penalty           = block_penalty + disagree_penalty
    adjusted          = clip(blended - penalty, 0, 1)

The base rule-based probability remains the dominant signal (default
weight 0.75). Apex contributes at most `APEX_V3_BLEND_WEIGHT` of the
blended value, so a 15-of-15 corroboration can only push a clean-spec GO
probability from 0.70 to 0.775 (+0.075) — no runaway amplification.

### Non-destructive guarantees

1. **When `eta_v3_voices` is absent**, `adjusted == base`. The reasoning
   string, primary/secondary drivers, and output payload shape are
   identical to the pre-Apex behaviour (the payload additionally carries
   `eta_v3.consumed = False`).

2. **Spec violations always dominate**. A MODIFY verdict from the
   rule-based gate stays MODIFY regardless of voice agreement. The fold
   only adjusts the quantitative probability; verdicts are never
   overturned.

3. **Malformed inputs are tolerated**. Non-int `voice_agree`, non-int
   `direction`, `None` blocked_reason — all are coerced/clamped without
   raising.

### Audit trail

Every invocation writes a summary to `output.payload["eta_v3"]`:

```json
{
  "consumed": true,
  "voice_agree": 11,
  "direction": 1,
  "direction_label": "LONG",
  "regime": "TREND",
  "fire_long": true,
  "fire_short": false,
  "fire_label": "FIRE_LONG",
  "blocked_reason": "",
  "base_probability": 0.70,
  "adjusted_probability": 0.7583,
  "delta": 0.0583,
  "blend_weight": 0.25,
  "penalty_applied": 0.0,
  "supporting": true,
  "strong_corroboration": false
}
```

The PM agent (stage 6) reads the whole agent_outputs dict for dissent
tally + final synthesis; the `eta_v3` key is there for it to inspect
without needing to re-read the payload.

### Reasoning-string change

When `eta_v3_voices` is consumed, the Quant reasoning appends:

> Apex V3 corroboration: agree=11/15, dir=LONG, regime=TREND, fire=FIRE_LONG; Δprob=+0.058.

When absent, the reasoning is unchanged.

## Tests

- `tests/level_1_unit/test_quant_eta_v3_consumption.py` — **20 new tests**
  - `TestNoApexVoices` (4): absent/string/malformed payload is a no-op on
    probability; spec violations dominate.
  - `TestApexFoldMath` (8): full agreement lift, zero agreement drag, block
    penalty, direction-disagreement penalty, penalty stacking, clipping
    at both bounds, direction label map.
  - `TestApexReasoningTrail` (4): reasoning suffix + tertiary_driver update
    when consumed; unchanged when absent.
  - `TestMalformedApexVoices` (4): string/out-of-range voice_agree clamped,
    non-int direction → 0, None blocked_reason → empty string.

- Full sweep: **632 passed, 2 skipped** (pre-existing Windows tempfile
  teardown quirks on SQLite tests are unchanged; eta_v3 tests 37/37 green).

## Windows portability fixes (drive-by)

The peer batch was written on Linux and the `🟢/🔴/🟡` emoji markers +
`—` em-dashes fried the cp1252 default on Windows. Fixes:

- `scripts/eta_v3_probe.py`, `eta_v3_enrich.py`, `eta_v3_bridge.py`,
  `run_all_phases.py` — `sys.stdout.reconfigure(encoding="utf-8")` in
  `contextlib.suppress` guard.
- All `write_text()` calls in the four Apex V3 scripts + `firm_bridge.py`
  + `run_all_phases.py` — now pass `encoding="utf-8"`.
- `scripts/eta_v3_bridge.py` — `EVAL_EXTRA_KWARGS` rewritten as a dict
  literal (ruff C408).

Orchestrator now runs to completion on Windows: `PYTHONUTF8=1
FIRM_CODE_PATH=... python scripts/run_all_phases.py` → **57/61 green**
(the 2-stage delta vs MANIFEST's 59/61 is `burn_in_72h` — uses Unix
`resource` module — and `crash_recovery` — SQLite tempfile handle on
Windows, pre-existing on both fronts).

## What still blocks batches 2 and 3

Per the MANIFEST:

2. **PM agent consuming `eta_v3_pm_final`** — straightforward now that
   the payload pipeline carries it. The PM agent can read
   `agent_outputs['quant'].payload['eta_v3']` for the full summary
   or `payload['eta_v3_pm_final']` for the engine's own aggregate.

3. **Meta-Firm wiring into the orchestrator** — requires
   `eta_v3_framework/python/firm_meta.py` to expose a callable surface
   that `scripts/run_all_phases.py` can fold into stage selection. The
   adapter interface is ready; meta-firm itself is the work.

## Contract reminder

The adapter is a PURE MAPPING LAYER. No trading decisions are made in
`src/mnq/eta_v3/adapter.py`. The Quant fold-in described here is the
*first* place an Apex V3 signal actually moves a probability estimate —
but it still cannot move a verdict (MODIFY/HOLD/KILL stay put) and
cannot bypass any downstream agent's veto. Red Team dissent, risk kill
conditions, macro overrides, microstructure blocks, and PM orchestration
are all unchanged and upstream of any Apex influence.
