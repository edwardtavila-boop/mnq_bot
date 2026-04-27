"""Out-of-sample validation of outcome-weighted gate weights.

Batch 11A. The Batch 10 OW weights were trained and evaluated on the
same 200-day sample. This script performs:

  1. **Train/test split** — 60/40 chronological split (first 120 days
     for training, last 80 for testing). No shuffle — preserves time order.
  2. **Walk-forward** — rolling 60-day train windows with 30-day steps,
     evaluating the next 30 days each time. Simulates how OW weights
     would perform if retrained periodically in production.

For each method, compares raw vs OW filtering on the TEST set only.
The key question: does OW filtering still outperform raw when evaluated
on data the weights never saw?

Output: ``reports/ow_validation.md``
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass
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
from mnq.gauntlet.outcome_weights import (  # noqa: E402
    GateDayRecord,
    compute_gate_weights,
    outcome_weighted_pass_rate,
)
from mnq.sim.layer2 import Layer2Engine  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE_SPEC = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
REPORT = REPO_ROOT / "reports" / "ow_validation.md"
VARIANTS = {cfg.name: cfg for cfg in _VARIANT_LIST}


@dataclass
class DayData:
    """Pre-computed per-day record with all needed fields."""

    day_idx: int
    regime: str
    pnl: float
    raw_pass_rate: float
    gate_record: GateDayRecord


def _peak_volume_bar_idx(bars: list) -> int:
    if not bars:
        return 0
    best_idx, best_vol = 0, 0
    for i, b in enumerate(bars):
        if b.volume > best_vol:
            best_vol = b.volume
            best_idx = i
    return best_idx


def _precompute_days(max_days: int = 200) -> list[DayData]:
    """Load bars, run sim + gauntlet on each day, return pre-computed records."""
    from strategy_v2 import ScriptedStrategyV2

    spec = load_spec(BASELINE_SPEC)
    days = _load_real_days(timeframe="1m", source="databento")

    rng = random.Random(42)
    if len(days) > max_days:
        sampled = sorted(rng.sample(range(len(days)), max_days))
        days = [days[i] for i in sampled]

    variant_cfg = VARIANTS["r5_real_wide_target"]
    results: list[DayData] = []

    for i, (regime, bars) in enumerate(days):
        if i % 50 == 0:
            print(f"  Precomputing day {i}/{len(days)}")

        strat = ScriptedStrategyV2(spec, cfg=variant_cfg)
        engine = Layer2Engine(spec, strat, seed=42)
        engine._rejection_p = 0.0
        ledger = engine.run(bars)
        day_pnl = sum(float(tr.pnl_dollars) for tr in ledger.trades)

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

        g = gauntlet_day_score(bars, regime=regime if regime != "unknown" else None)

        results.append(
            DayData(
                day_idx=i,
                regime=regime,
                pnl=round(day_pnl, 2),
                raw_pass_rate=g.pass_rate,
                gate_record=GateDayRecord(
                    day_idx=i,
                    gate_passed=gate_passed,
                    gate_scores=gate_scores,
                    pnl=round(day_pnl, 2),
                ),
            )
        )

    return results


def _evaluate_filtering(
    days: list[DayData],
    gate_weights: dict[str, float] | None,
    skip_threshold: float = 0.50,
    reduce_threshold: float = 0.67,
    reduced_size: float = 0.5,
) -> dict[str, object]:
    """Evaluate raw vs OW filtering on a set of days.

    Returns dict with full/reduced/skipped counts, PnL, and effective PnL.
    """
    full_n = full_pnl = 0
    reduced_n = reduced_pnl = 0
    skipped_n = skipped_pnl = 0.0

    for d in days:
        if gate_weights:
            pr = outcome_weighted_pass_rate(d.gate_record.gate_passed, gate_weights)
        else:
            pr = d.raw_pass_rate

        if pr < skip_threshold:
            skipped_n += 1
            skipped_pnl += d.pnl
        elif pr < reduce_threshold:
            reduced_n += 1
            reduced_pnl += d.pnl
        else:
            full_n += 1
            full_pnl += d.pnl

    effective = full_pnl + reduced_pnl * reduced_size
    return {
        "full_n": full_n,
        "full_pnl": round(full_pnl, 2),
        "reduced_n": reduced_n,
        "reduced_pnl": round(reduced_pnl, 2),
        "skipped_n": skipped_n,
        "skipped_pnl": round(skipped_pnl, 2),
        "effective_pnl": round(effective, 2),
        "total_pnl": round(sum(d.pnl for d in days), 2),
    }


def _fmt_eval(label: str, e: dict) -> list[str]:
    """Format evaluation result as markdown table rows."""
    lines = [
        f"**{label}**",
        "",
        "| Action | Days | PnL | Avg PnL/day |",
        "|---|---:|---:|---:|",
        f"| Full | {e['full_n']} | ${e['full_pnl']:+,.2f} | ${e['full_pnl'] / max(1, e['full_n']):+,.2f} |",
        f"| Reduced | {e['reduced_n']} | ${e['reduced_pnl']:+,.2f} | ${e['reduced_pnl'] / max(1, e['reduced_n']):+,.2f} |",
        f"| Skipped | {e['skipped_n']} | ${e['skipped_pnl']:+,.2f} | ${e['skipped_pnl'] / max(1, e['skipped_n']):+,.2f} |",
        "",
        f"Effective PnL: ${e['effective_pnl']:+,.2f} (total: ${e['total_pnl']:+,.2f})",
        "",
    ]
    return lines


def main() -> int:
    print("Precomputing 200 days...")
    all_days = _precompute_days(200)
    print(f"  {len(all_days)} days ready")

    lines = [
        "# OW Validation — Out-of-Sample Testing",
        "",
        f"Sample: {len(all_days)} days",
        "",
    ]

    # ---------------------------------------------------------------
    # Test 1: Chronological 60/40 train/test split
    # ---------------------------------------------------------------
    split_idx = int(len(all_days) * 0.6)
    train_days = all_days[:split_idx]
    test_days = all_days[split_idx:]

    train_records = [d.gate_record for d in train_days]
    ow_weights = compute_gate_weights(train_records, min_samples=5)

    raw_test = _evaluate_filtering(test_days, gate_weights=None)
    ow_test = _evaluate_filtering(test_days, gate_weights=ow_weights.gate_weights)

    lines.extend(
        [
            "## Test 1: Chronological 60/40 Split",
            "",
            f"Train: {len(train_days)} days (first 60%), Test: {len(test_days)} days (last 40%)",
            "",
            "### Weights learned from training set",
            "",
            "| Gate | Weight | Correlation |",
            "|---|---:|---:|",
        ]
    )
    for r in sorted(ow_weights.gate_results, key=lambda x: -x.weight):
        if r.weight > 0 or r.raw_correlation < -0.01:
            lines.append(f"| {r.name} | {r.weight:.4f} | {r.raw_correlation:+.4f} |")

    lines.extend(["", "### Test set results (unseen data)", ""])
    lines.extend(_fmt_eval("Raw pass_rate", raw_test))
    lines.extend(_fmt_eval("Outcome-weighted", ow_test))

    delta_split = ow_test["effective_pnl"] - raw_test["effective_pnl"]
    lines.extend(
        [
            f"**OW vs Raw delta on test set: ${delta_split:+,.2f}**",
            "",
        ]
    )

    # ---------------------------------------------------------------
    # Test 2: Walk-forward with rolling windows
    # ---------------------------------------------------------------
    train_window = 60
    test_window = 30
    step = 30

    lines.extend(
        [
            "## Test 2: Walk-Forward (rolling retrain)",
            "",
            f"Train window: {train_window} days, Test window: {test_window} days, Step: {step} days",
            "",
            "| Fold | Train | Test | Raw Eff PnL | OW Eff PnL | Delta | Top OW Gate |",
            "|---:|---|---|---:|---:|---:|---|",
        ]
    )

    fold = 0
    wf_raw_total = 0.0
    wf_ow_total = 0.0
    start = 0

    while start + train_window + test_window <= len(all_days):
        fold += 1
        train_slice = all_days[start : start + train_window]
        test_slice = all_days[start + train_window : start + train_window + test_window]

        fold_records = [d.gate_record for d in train_slice]
        fold_weights = compute_gate_weights(fold_records, min_samples=3)

        fold_raw = _evaluate_filtering(test_slice, gate_weights=None)
        fold_ow = _evaluate_filtering(test_slice, gate_weights=fold_weights.gate_weights)

        fold_delta = fold_ow["effective_pnl"] - fold_raw["effective_pnl"]
        wf_raw_total += fold_raw["effective_pnl"]
        wf_ow_total += fold_ow["effective_pnl"]

        # Find top OW gate for this fold
        top_gate = (
            max(fold_weights.gate_results, key=lambda r: r.weight)
            if fold_weights.gate_results
            else None
        )
        top_name = (
            f"{top_gate.name} ({top_gate.weight:.3f})"
            if top_gate and top_gate.weight > 0
            else "none"
        )

        train_range = f"{start}–{start + train_window - 1}"
        test_range = f"{start + train_window}–{start + train_window + test_window - 1}"

        lines.append(
            f"| {fold} | {train_range} | {test_range} | "
            f"${fold_raw['effective_pnl']:+,.2f} | ${fold_ow['effective_pnl']:+,.2f} | "
            f"${fold_delta:+,.2f} | {top_name} |"
        )

        start += step

    wf_delta = wf_ow_total - wf_raw_total
    lines.extend(
        [
            "",
            f"**Walk-forward totals:** Raw ${wf_raw_total:+,.2f}, OW ${wf_ow_total:+,.2f}, Delta ${wf_delta:+,.2f}",
            "",
        ]
    )

    # ---------------------------------------------------------------
    # Test 3: Leave-one-out sensitivity
    # ---------------------------------------------------------------
    lines.extend(
        [
            "## Test 3: Jackknife Sensitivity",
            "",
            "Drop each gate from the OW weight set (set to 0) and measure impact.",
            "",
        ]
    )

    # Full OW weights (all days)
    full_records = [d.gate_record for d in all_days]
    full_weights = compute_gate_weights(full_records, min_samples=5)
    base_eval = _evaluate_filtering(all_days, gate_weights=full_weights.gate_weights)
    base_eff = base_eval["effective_pnl"]

    active_gates = [r for r in full_weights.gate_results if r.weight > 0]
    if active_gates:
        lines.extend(
            [
                "| Dropped Gate | Eff PnL | vs Full OW | Impact |",
                "|---|---:|---:|---|",
            ]
        )
        for gate_r in sorted(active_gates, key=lambda x: -x.weight):
            modified_weights = dict(full_weights.gate_weights)
            modified_weights[gate_r.name] = 0.0
            drop_eval = _evaluate_filtering(all_days, gate_weights=modified_weights)
            drop_eff = drop_eval["effective_pnl"]
            impact_val = drop_eff - base_eff
            impact = "HURTS" if impact_val < -1.0 else ("HELPS" if impact_val > 1.0 else "NEUTRAL")
            lines.append(f"| {gate_r.name} | ${drop_eff:+,.2f} | ${impact_val:+,.2f} | {impact} |")
        lines.append("")
    else:
        lines.append("No active gates with weight > 0 to drop.\n")

    # ---------------------------------------------------------------
    # Verdict
    # ---------------------------------------------------------------
    lines.extend(
        [
            "## Verdict",
            "",
        ]
    )

    if delta_split > 0 and wf_delta > 0:
        lines.append(
            f"OW filtering outperforms raw on BOTH out-of-sample tests "
            f"(split: ${delta_split:+,.2f}, walk-forward: ${wf_delta:+,.2f}). "
            f"The outcome-weighted recalibration generalizes beyond the training set."
        )
    elif delta_split > 0 or wf_delta > 0:
        lines.append(
            f"Mixed results: split test ${delta_split:+,.2f}, walk-forward ${wf_delta:+,.2f}. "
            f"OW weights show some generalization but are not consistently better. "
            f"Consider wider training windows or ensemble approaches."
        )
    else:
        lines.append(
            f"OW filtering does NOT outperform on out-of-sample data "
            f"(split: ${delta_split:+,.2f}, WF: ${wf_delta:+,.2f}). "
            f"The in-sample improvement was likely overfitting. Keep raw pass_rate "
            f"as the default gate until more data accumulates."
        )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
