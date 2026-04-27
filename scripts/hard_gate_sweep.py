"""Hard-gate threshold sweep on Databento sample.

Batch 9B. Sweeps the gauntlet hard-gate skip/reduce thresholds across
the multi-year Databento dataset to find the configuration that maximizes
PnL through selective filtering.

Unlike the V16 delta-blend sweep (Batch 8A, flat result), this sweep
directly blocks/reduces days based on gauntlet pass_rate — a much
stronger filtering mechanism.

Output: ``reports/hard_gate_sweep.md``
"""

from __future__ import annotations

import argparse
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

import random  # noqa: E402

from strategy_ab import _load_real_days  # noqa: E402
from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

from mnq.gauntlet.day_aggregate import gauntlet_day_score  # noqa: E402
from mnq.gauntlet.hard_gate import (  # noqa: E402
    GauntletHardGateConfig,
    gauntlet_hard_gate,
)
from mnq.sim.layer2 import Layer2Engine  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE_SPEC = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
DEFAULT_REPORT = REPO_ROOT / "reports" / "hard_gate_sweep.md"
VARIANTS = {cfg.name: cfg for cfg in _VARIANT_LIST}

# Sweep grid: (skip_threshold, reduce_threshold)
THRESHOLD_GRID = [
    (0.00, 0.00),  # no filtering (baseline)
    (0.25, 0.50),  # loose
    (0.33, 0.50),  # light
    (0.40, 0.60),  # default
    (0.50, 0.67),  # moderate
    (0.50, 0.75),  # moderate-tight
    (0.60, 0.75),  # tight
    (0.67, 0.83),  # very tight
    (0.75, 0.90),  # aggressive
]


@dataclass
class SweepResult:
    skip_threshold: float
    reduce_threshold: float
    total_pnl: float
    n_trades: int
    n_full: int
    n_reduced: int
    n_skip: int
    avg_pnl_per_trade: float
    n_days: int
    block_rate: float


def _run_sweep(
    *,
    filtered_name: str = "r5_real_wide_target",
    seed: int = 42,
    max_days: int = 200,
) -> list[SweepResult]:
    from strategy_v2 import ScriptedStrategyV2

    spec = load_spec(BASELINE_SPEC)

    print("Loading Databento days...")
    t0 = time.time()
    days = _load_real_days(timeframe="1m", source="databento")
    print(f"  Loaded {len(days)} days in {time.time() - t0:.1f}s")

    if len(days) > max_days:
        rng = random.Random(seed)
        sampled = sorted(rng.sample(range(len(days)), max_days))
        days = [days[i] for i in sampled]
        print(f"  Sampled {max_days} days")

    variant_cfg = VARIANTS[filtered_name]

    # Pre-compute ledgers + gauntlet day scores
    print("Pre-computing ledgers and gauntlet scores...")
    t0 = time.time()
    ledgers = []
    day_scores = []
    for i, (regime, bars) in enumerate(days):
        strat = ScriptedStrategyV2(spec, cfg=variant_cfg)
        engine = Layer2Engine(spec, strat, seed=seed)
        engine._rejection_p = 0.0
        ledgers.append(engine.run(bars))

        g_score = gauntlet_day_score(
            bars,
            regime=regime if regime != "unknown" else None,
        )
        day_scores.append(g_score)

        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(days)} days")

    print(f"  Done in {time.time() - t0:.1f}s")

    results = []
    for skip_t, reduce_t in THRESHOLD_GRID:
        cfg = GauntletHardGateConfig(
            skip_threshold=skip_t,
            reduce_threshold=reduce_t,
            critical_gates=frozenset(),  # disable critical gate check for clean sweep
        )

        total_pnl = 0.0
        n_trades = 0
        n_full = 0
        n_reduced = 0
        n_skip = 0

        for g_score, ledger in zip(day_scores, ledgers, strict=True):
            dec = gauntlet_hard_gate(g_score, config=cfg)
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
                    from decimal import Decimal

                    eff_qty = max(1, int(round(tr.qty * mult)))
                    scale = Decimal(eff_qty) / Decimal(tr.qty) if tr.qty else Decimal(1)
                    total_pnl += float(tr.pnl_dollars * scale)
                    n_trades += 1

        avg = total_pnl / n_trades if n_trades > 0 else 0.0
        block_rate = n_skip / len(days) if days else 0.0

        results.append(
            SweepResult(
                skip_threshold=skip_t,
                reduce_threshold=reduce_t,
                total_pnl=round(total_pnl, 2),
                n_trades=n_trades,
                n_full=n_full,
                n_reduced=n_reduced,
                n_skip=n_skip,
                avg_pnl_per_trade=round(avg, 2),
                n_days=len(days),
                block_rate=round(block_rate, 3),
            )
        )
        print(
            f"  skip={skip_t:.2f} reduce={reduce_t:.2f} → "
            f"PnL=${total_pnl:+,.2f}, trades={n_trades}, "
            f"full={n_full}/red={n_reduced}/skip={n_skip} "
            f"(block={block_rate:.1%})"
        )

    return results


def _render(results: list[SweepResult]) -> str:
    n_days = results[0].n_days if results else 0
    lines = [f"# Gauntlet Hard-Gate Threshold Sweep — {n_days} days", ""]
    lines.append("Batch 9B. Sweeps gauntlet hard-gate skip/reduce thresholds.")
    lines.append("Baseline: (0.00, 0.00) = no filtering.")
    lines.append("")
    lines.append(
        "| Skip | Reduce | PnL | Trades | Full | Reduced | Skip | Block% | Avg PnL/trade |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    best = max(results, key=lambda r: r.total_pnl)
    baseline = results[0]  # (0.00, 0.00)

    for r in results:
        marker = " ★" if r is best else ""
        lines.append(
            f"| {r.skip_threshold:.2f} | {r.reduce_threshold:.2f} | "
            f"${r.total_pnl:+,.2f} | {r.n_trades} | "
            f"{r.n_full} | {r.n_reduced} | {r.n_skip} | "
            f"{r.block_rate:.1%} | ${r.avg_pnl_per_trade:+,.2f}{marker} |"
        )

    lines.append("")
    diff = best.total_pnl - baseline.total_pnl
    lines.append(
        f"**Best config:** skip={best.skip_threshold:.2f}, reduce={best.reduce_threshold:.2f}"
    )
    lines.append(
        f"- PnL: ${best.total_pnl:+,.2f} (baseline: ${baseline.total_pnl:+,.2f}, Δ=${diff:+,.2f})"
    )
    lines.append(f"- Block rate: {best.block_rate:.1%}")
    lines.append(f"- Avg PnL/trade: ${best.avg_pnl_per_trade:+,.2f}")
    lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    if diff > 0:
        lines.append(
            f"The hard-gate at skip={best.skip_threshold:.2f}/reduce={best.reduce_threshold:.2f} "
            f"improved PnL by ${diff:+,.2f} over unfiltered baseline. "
            f"This confirms the gauntlet adds filtering value when applied as a "
            f"direct pass/fail gate rather than via the delta-blend path."
        )
    elif diff == 0:
        lines.append(
            "No threshold configuration improved PnL over the unfiltered baseline. "
            "The gauntlet pass_rate distribution may be too uniform (all days pass at "
            "similar rates), or the strategy's losses aren't concentrated on "
            "low-pass-rate days."
        )
    else:
        lines.append(
            "Filtering reduced PnL — the hard-gate is blocking profitable days. "
            "This suggests the gauntlet gates are miscalibrated: some gates fail on "
            "days that are actually profitable. Gate-level attribution needed."
        )

    # Show pass_rate distribution
    lines.append("")
    lines.append(f"_Sample: {n_days} Databento RTH days._")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hard-gate threshold sweep (Batch 9B).")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--max-days", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    print("=== Hard-gate threshold sweep (Batch 9B) ===")
    results = _run_sweep(seed=args.seed, max_days=args.max_days)
    md = _render(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
