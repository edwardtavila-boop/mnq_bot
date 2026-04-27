"""Per-gate PnL attribution — which gates predict profit, which destroy value?

Batch 10A/10B. Runs the 200-day Databento sample, collects per-gate
verdicts + realized PnL, then trains outcome weights using
``compute_gate_weights()``.

Produces:
  - ``reports/gate_pnl_attribution.md`` — per-gate correlation table
  - ``data/outcome_gate_weights.json`` — serialized weights for runtime use

Key questions answered:
  1. Which gates have positive correlation with PnL? (keep/upweight)
  2. Which gates are anti-correlated? (zero-weight or invert)
  3. What does the outcome-weighted hard-gate look like vs raw pass_rate?
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from strategy_ab import _load_real_days  # noqa: E402
from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

from mnq.gauntlet.bridge import context_from_bars  # noqa: E402
from mnq.gauntlet.day_aggregate import gauntlet_day_score  # noqa: E402
from mnq.gauntlet.gates.gauntlet12 import run_gauntlet  # noqa: E402
from mnq.gauntlet.hard_gate import (  # noqa: E402
    GauntletHardGateConfig,
)
from mnq.gauntlet.outcome_weights import (  # noqa: E402
    GateDayRecord,
    compute_gate_weights,
    outcome_weighted_pass_rate,
)
from mnq.sim.layer2 import Layer2Engine  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE_SPEC = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
REPORT = REPO_ROOT / "reports" / "gate_pnl_attribution.md"
WEIGHTS_FILE = REPO_ROOT / "data" / "outcome_gate_weights.json"
VARIANTS = {cfg.name: cfg for cfg in _VARIANT_LIST}


def _peak_volume_bar_idx(bars: list) -> int:
    if not bars:
        return 0
    best_idx = 0
    best_vol = 0
    for i, b in enumerate(bars):
        if b.volume > best_vol:
            best_vol = b.volume
            best_idx = i
    return best_idx


def main() -> int:
    from strategy_v2 import ScriptedStrategyV2

    spec = load_spec(BASELINE_SPEC)
    days = _load_real_days(timeframe="1m", source="databento")

    rng = random.Random(42)
    if len(days) > 200:
        sampled = sorted(rng.sample(range(len(days)), 200))
        days = [days[i] for i in sampled]

    variant_cfg = VARIANTS["r5_real_wide_target"]

    records: list[GateDayRecord] = []
    raw_pass_rates: list[float] = []
    pnl_list: list[float] = []

    print(f"Processing {len(days)} days...")

    for i, (regime, bars) in enumerate(days):
        if i % 50 == 0:
            print(f"  Day {i}/{len(days)}")

        # Run sim for PnL
        strat = ScriptedStrategyV2(spec, cfg=variant_cfg)
        engine = Layer2Engine(spec, strat, seed=42)
        engine._rejection_p = 0.0
        ledger = engine.run(bars)
        day_pnl = sum(float(tr.pnl_dollars) for tr in ledger.trades)

        # Run gauntlet for per-gate verdicts
        bar_idx = _peak_volume_bar_idx(bars)
        ctx = context_from_bars(
            bars,
            bar_idx,
            side="long",
            regime=regime if regime != "unknown" else None,
        )
        verdicts = run_gauntlet(ctx)

        gate_passed = {v.name: v.pass_ for v in verdicts}
        gate_scores = {v.name: v.score for v in verdicts}

        records.append(
            GateDayRecord(
                day_idx=i,
                gate_passed=gate_passed,
                gate_scores=gate_scores,
                pnl=round(day_pnl, 2),
            )
        )

        g = gauntlet_day_score(bars, regime=regime if regime != "unknown" else None)
        raw_pass_rates.append(g.pass_rate)
        pnl_list.append(round(day_pnl, 2))

    # Compute outcome weights
    weights = compute_gate_weights(records, min_samples=5)

    # Compute outcome-weighted pass rates for comparison
    ow_pass_rates = []
    for r in records:
        ow_pr = outcome_weighted_pass_rate(r.gate_passed, weights.gate_weights)
        ow_pass_rates.append(ow_pr)

    # Compare filtering with raw vs outcome-weighted pass rate
    # Using same thresholds as Batch 9B
    cfg = GauntletHardGateConfig(
        skip_threshold=0.50,
        reduce_threshold=0.67,
        critical_gates=frozenset(),
    )

    raw_full_pnl = 0.0
    raw_reduced_pnl = 0.0
    raw_skipped_pnl = 0.0
    raw_full_n = 0
    raw_reduced_n = 0
    raw_skipped_n = 0

    ow_full_pnl = 0.0
    ow_reduced_pnl = 0.0
    ow_skipped_pnl = 0.0
    ow_full_n = 0
    ow_reduced_n = 0
    ow_skipped_n = 0

    for r, raw_pr, ow_pr in zip(records, raw_pass_rates, ow_pass_rates, strict=True):
        # Raw filtering
        if raw_pr < cfg.skip_threshold:
            raw_skipped_pnl += r.pnl
            raw_skipped_n += 1
        elif raw_pr < cfg.reduce_threshold:
            raw_reduced_pnl += r.pnl
            raw_reduced_n += 1
        else:
            raw_full_pnl += r.pnl
            raw_full_n += 1

        # Outcome-weighted filtering
        if ow_pr < cfg.skip_threshold:
            ow_skipped_pnl += r.pnl
            ow_skipped_n += 1
        elif ow_pr < cfg.reduce_threshold:
            ow_reduced_pnl += r.pnl
            ow_reduced_n += 1
        else:
            ow_full_pnl += r.pnl
            ow_full_n += 1

    # Sort results by weight for report
    sorted_results = sorted(weights.gate_results, key=lambda r: r.weight, reverse=True)

    lines = [
        "# Per-Gate PnL Attribution — Outcome-Weighted Recalibration",
        "",
        f"Sample: {len(records)} days, total PnL: ${weights.total_pnl:+,.2f}",
        f"Method: {weights.method}",
        "",
        "## Per-gate outcome weights",
        "",
        "| Gate | Weight | Correlation | Pass→PnL | Fail→PnL | Pass N | Fail N | IV |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for r in sorted_results:
        lines.append(
            f"| {r.name} | {r.weight:.3f} | {r.raw_correlation:+.3f} | "
            f"${r.pass_pnl_mean:+,.2f} | ${r.fail_pnl_mean:+,.2f} | "
            f"{r.pass_count} | {r.fail_count} | {r.information_value:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Gate classification",
            "",
        ]
    )

    value_adding = [r for r in sorted_results if r.weight > 0.05]
    neutral = [r for r in sorted_results if 0 < r.weight <= 0.05]
    value_destroying = [r for r in sorted_results if r.weight == 0 and r.raw_correlation < -0.01]
    insufficient = [
        r
        for r in sorted_results
        if r.weight == 0 and r.raw_correlation >= -0.01 and r.fail_count < 5
    ]

    if value_adding:
        lines.append(
            f"**Value-adding** (positive PnL correlation, weight > 0.05): "
            f"{', '.join(r.name for r in value_adding)}"
        )
        lines.append("")
    if neutral:
        lines.append(
            f"**Neutral** (weak positive correlation, weight ≤ 0.05): "
            f"{', '.join(r.name for r in neutral)}"
        )
        lines.append("")
    if value_destroying:
        lines.append(
            f"**Value-destroying** (anti-correlated with PnL): "
            f"{', '.join(r.name for r in value_destroying)}"
        )
        lines.append("")
    if insufficient:
        lines.append(
            f"**Insufficient data** (< 5 failures, can't evaluate): "
            f"{', '.join(r.name for r in insufficient)}"
        )
        lines.append("")

    lines.extend(
        [
            "## Filtering comparison: raw vs outcome-weighted",
            "",
            f"Thresholds: skip={cfg.skip_threshold}, reduce={cfg.reduce_threshold}",
            "",
            "### Raw pass_rate filtering",
            "",
            "| Action | Days | PnL | Avg PnL/day |",
            "|---|---:|---:|---:|",
            f"| Full | {raw_full_n} | ${raw_full_pnl:+,.2f} | ${raw_full_pnl / max(1, raw_full_n):+,.2f} |",
            f"| Reduced | {raw_reduced_n} | ${raw_reduced_pnl:+,.2f} | ${raw_reduced_pnl / max(1, raw_reduced_n):+,.2f} |",
            f"| Skipped | {raw_skipped_n} | ${raw_skipped_pnl:+,.2f} | ${raw_skipped_pnl / max(1, raw_skipped_n):+,.2f} |",
            "",
            "### Outcome-weighted pass_rate filtering",
            "",
            "| Action | Days | PnL | Avg PnL/day |",
            "|---|---:|---:|---:|",
            f"| Full | {ow_full_n} | ${ow_full_pnl:+,.2f} | ${ow_full_pnl / max(1, ow_full_n):+,.2f} |",
            f"| Reduced | {ow_reduced_n} | ${ow_reduced_pnl:+,.2f} | ${ow_reduced_pnl / max(1, ow_reduced_n):+,.2f} |",
            f"| Skipped | {ow_skipped_n} | ${ow_skipped_pnl:+,.2f} | ${ow_skipped_pnl / max(1, ow_skipped_n):+,.2f} |",
            "",
        ]
    )

    # Compute effective filtering value
    ow_kept_pnl = ow_full_pnl + ow_reduced_pnl * 0.5  # reduced = half size
    raw_kept_pnl = raw_full_pnl + raw_reduced_pnl * 0.5
    ow_lost_pnl = ow_skipped_pnl + ow_reduced_pnl * 0.5
    raw_lost_pnl = raw_skipped_pnl + raw_reduced_pnl * 0.5

    lines.extend(
        [
            "### Effective PnL after filtering",
            "",
            f"- Raw filtering effective PnL: ${raw_kept_pnl:+,.2f} (lost ${raw_lost_pnl:+,.2f} to skip/reduce)",
            f"- OW filtering effective PnL: ${ow_kept_pnl:+,.2f} (lost ${ow_lost_pnl:+,.2f} to skip/reduce)",
            f"- Delta: ${ow_kept_pnl - raw_kept_pnl:+,.2f}",
            "",
            "## Interpretation",
            "",
        ]
    )

    if ow_kept_pnl > raw_kept_pnl:
        lines.append(
            f"Outcome-weighted filtering outperforms raw filtering by "
            f"${ow_kept_pnl - raw_kept_pnl:+,.2f}. The recalibration successfully "
            f"redirects the gauntlet toward PnL-correlated signals."
        )
    elif abs(ow_kept_pnl - raw_kept_pnl) < 1.0:
        lines.append(
            "Outcome-weighted filtering performs similarly to raw filtering. "
            "The gate correlations may be too weak on this sample to produce "
            "meaningful differentiation. Consider: (a) more training days, "
            "(b) continuous scores instead of binary pass/fail, or "
            "(c) accepting that the gauntlet adds no filtering value at "
            "these thresholds."
        )
    else:
        lines.append(
            f"Outcome-weighted filtering underperforms raw by "
            f"${raw_kept_pnl - ow_kept_pnl:+,.2f}. This may indicate overfitting "
            f"or that the gate-PnL relationship is noisy. Consider wider training "
            f"window or different threshold levels."
        )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))

    # Serialize weights for runtime use
    weights_data = {
        "method": weights.method,
        "n_days": weights.n_days,
        "total_pnl": weights.total_pnl,
        "gate_weights": weights.gate_weights,
        "gate_details": [
            {
                "name": r.name,
                "weight": round(r.weight, 4),
                "raw_correlation": round(r.raw_correlation, 4),
                "pass_pnl_mean": round(r.pass_pnl_mean, 2),
                "fail_pnl_mean": round(r.fail_pnl_mean, 2),
                "pass_count": r.pass_count,
                "fail_count": r.fail_count,
                "information_value": round(r.information_value, 4),
            }
            for r in sorted_results
        ],
    }

    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_FILE.write_text(json.dumps(weights_data, indent=2) + "\n")
    print(f"\nwrote {REPORT}")
    print(f"wrote {WEIGHTS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
