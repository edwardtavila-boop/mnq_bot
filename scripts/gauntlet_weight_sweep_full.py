"""Full-sample gauntlet V16 weight sweep on the Databento 1,724-day tape.

Batch 8A. Same logic as ``gauntlet_weight_sweep.py`` (Batch 5D) but runs
across the full multi-year Databento dataset. Uses sampling for speed:
evaluates a random subset of days if the full set exceeds ``max_days``.

On the 15-day sample the sweep was flat (all weights produced identical
PnL). A larger sample should reveal threshold-crossing events where the
gauntlet weight actually shifts a day from allow→block or vice versa.

Output: ``reports/gauntlet_weight_sweep_full.md``
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from real_eta_driver import day_pm_output_from_real_apex  # noqa: E402
from strategy_ab import _load_real_days  # noqa: E402
from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

from mnq.eta_v3.gate import apex_gate  # noqa: E402
from mnq.gauntlet.day_aggregate import gauntlet_day_score  # noqa: E402
from mnq.sim.layer2 import Layer2Engine  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE_SPEC = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
DEFAULT_REPORT = REPO_ROOT / "reports" / "gauntlet_weight_sweep_full.md"
VARIANTS = {cfg.name: cfg for cfg in _VARIANT_LIST}
WEIGHTS_TO_SWEEP = [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]


@dataclass
class SweepResult:
    weight: float
    total_pnl: float
    n_trades: int
    n_full: int
    n_reduced: int
    n_skip: int
    avg_pnl_per_trade: float
    n_days: int


def _run_sweep(
    *,
    filtered_name: str = "r5_real_wide_target",
    seed: int = 42,
    max_days: int = 200,
    days_tail: int | None = None,
) -> list[SweepResult]:
    from strategy_v2 import ScriptedStrategyV2

    spec = load_spec(BASELINE_SPEC)

    print(f"Loading Databento days (tail={days_tail})...")
    t0 = time.time()
    days = _load_real_days(timeframe="1m", source="databento", days_tail=days_tail)
    print(f"  Loaded {len(days)} days in {time.time() - t0:.1f}s")

    # Sample if too many
    if len(days) > max_days:
        rng = random.Random(seed)
        sampled_indices = sorted(rng.sample(range(len(days)), max_days))
        days = [days[i] for i in sampled_indices]
        print(f"  Sampled {max_days} days for speed")

    variant_cfg = VARIANTS[filtered_name]

    # Pre-compute all ledgers + gauntlet deltas (shared across weights)
    print("Pre-computing ledgers and gauntlet scores...")
    t0 = time.time()
    ledgers = []
    g_deltas = []
    for i, (regime, bars) in enumerate(days):
        strat = ScriptedStrategyV2(spec, cfg=variant_cfg)
        engine = Layer2Engine(spec, strat, seed=seed)
        engine._rejection_p = 0.0
        ledgers.append(engine.run(bars))

        g_score = gauntlet_day_score(
            bars,
            regime=regime if regime != "unknown" else None,
        )
        g_deltas.append(g_score.delta)

        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(days)} days processed")

    print(f"  Pre-computation done in {time.time() - t0:.1f}s")

    # Sweep weights
    results = []
    for weight in WEIGHTS_TO_SWEEP:
        total_pnl = 0.0
        n_trades = 0
        n_full = 0
        n_reduced = 0
        n_skip = 0

        for (_regime, bars), ledger, g_delta in zip(days, ledgers, g_deltas, strict=True):
            pm_out = day_pm_output_from_real_apex(
                bars,
                gauntlet_delta=g_delta if weight > 0 else None,
                gauntlet_weight=weight,
            )
            dec = apex_gate(pm_out)
            action = dec["action"]
            mult = float(dec["size_mult"])

            if action == "full":
                n_full += 1
            elif action == "reduced":
                n_reduced += 1
            else:
                n_skip += 1

            if action != "skip" and ledger.n_trades > 0:
                for tr in ledger.trades:
                    eff_qty = max(1, int(round(tr.qty * mult)))
                    from decimal import Decimal
                    scale = Decimal(eff_qty) / Decimal(tr.qty) if tr.qty else Decimal(1)
                    total_pnl += float(tr.pnl_dollars * scale)
                    n_trades += 1

        avg = total_pnl / n_trades if n_trades > 0 else 0.0
        results.append(SweepResult(
            weight=weight,
            total_pnl=round(total_pnl, 2),
            n_trades=n_trades,
            n_full=n_full,
            n_reduced=n_reduced,
            n_skip=n_skip,
            avg_pnl_per_trade=round(avg, 2),
            n_days=len(days),
        ))
        print(f"  w={weight:.2f} → PnL=${total_pnl:+,.2f}, "
              f"trades={n_trades}, full={n_full}/red={n_reduced}/skip={n_skip}")

    return results


def _render(results: list[SweepResult]) -> str:
    n_days = results[0].n_days if results else 0
    lines = [f"# Gauntlet V16 Weight Sweep — Full Sample ({n_days} days)", ""]
    lines.append("Batch 8A. Evaluates gauntlet weight across the full Databento tape.")
    lines.append("Weight 0.00 = pure Apex (baseline). Higher = more gauntlet influence.")
    lines.append("")
    lines.append("| Weight | PnL | Trades | Full | Reduced | Skip | Avg PnL/trade |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")

    best = max(results, key=lambda r: r.total_pnl)
    for r in results:
        marker = " ★" if r is best else ""
        lines.append(
            f"| {r.weight:.2f} | ${r.total_pnl:+,.2f} | {r.n_trades} | "
            f"{r.n_full} | {r.n_reduced} | {r.n_skip} | "
            f"${r.avg_pnl_per_trade:+,.2f}{marker} |"
        )

    lines.append("")
    lines.append(f"**Best weight:** {best.weight:.2f} "
                 f"(PnL ${best.total_pnl:+,.2f}, {best.n_trades} trades)")
    lines.append("")

    # Compare best vs baseline (w=0.00)
    baseline = next(r for r in results if r.weight == 0.0)
    diff = best.total_pnl - baseline.total_pnl
    lines.append("## Comparison vs baseline (w=0.00)")
    lines.append("")
    lines.append(f"- Baseline PnL: ${baseline.total_pnl:+,.2f}")
    lines.append(f"- Best PnL: ${best.total_pnl:+,.2f}")
    lines.append(f"- Delta: ${diff:+,.2f}")
    if baseline.total_pnl != 0:
        pct = diff / abs(baseline.total_pnl) * 100
        lines.append(f"- Improvement: {pct:+.1f}%")
    lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    if diff > 0 and best.weight > 0:
        lines.append(
            f"Gauntlet weight {best.weight:.2f} improved PnL by ${diff:+,.2f} "
            f"over pure Apex. The gauntlet's binary gate filters are adding "
            f"signal beyond the continuous V1-V15 voices on the full sample."
        )
    elif diff <= 0 or best.weight == 0.0:
        lines.append(
            "Pure Apex (weight 0.00) was optimal or tied on the full sample. "
            "The gauntlet does not improve PnL at any weight. This is consistent "
            "with the 15-day result — gate thresholds may need recalibration, "
            "or the gauntlet's value is in risk reduction (fewer catastrophic days) "
            "rather than aggregate PnL lift."
        )
    lines.append("")
    lines.append(f"_Sample: {n_days} Databento RTH days._")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Full-sample V16 weight sweep (Batch 8A).")
    parser.add_argument("--filtered", type=str, default="r5_real_wide_target")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-days", type=int, default=200,
                        help="Max days to evaluate (random sample if exceeded)")
    parser.add_argument("--days-tail", type=int, default=None,
                        help="Only use the last N days from the tape")
    args = parser.parse_args(argv)

    print("=== Full-sample Gauntlet V16 weight sweep (Batch 8A) ===")
    results = _run_sweep(
        filtered_name=args.filtered,
        seed=args.seed,
        max_days=args.max_days,
        days_tail=args.days_tail,
    )
    md = _render(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
