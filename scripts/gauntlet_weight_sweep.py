"""Walk-forward gauntlet weight sweep.

Batch 5D. Evaluates different gauntlet_weight values (the V16 blend
factor) across the 15-day sample to find the weight that maximizes
risk-adjusted PnL while maintaining selective filtering.

Sweeps gauntlet_weight from 0.0 (pure Apex) to 0.40 (heavy gauntlet)
and reports: PnL, trade count, block rate, avg PnL/trade.

Output: ``reports/gauntlet_weight_sweep.md``
"""

from __future__ import annotations

import argparse
import sys
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
DEFAULT_REPORT = REPO_ROOT / "reports" / "gauntlet_weight_sweep.md"
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


def _run_sweep(
    *,
    filtered_name: str = "r5_real_wide_target",
    seed: int = 0,
) -> list[SweepResult]:
    from strategy_v2 import ScriptedStrategyV2

    spec = load_spec(BASELINE_SPEC)
    days = _load_real_days(timeframe="1m")
    variant_cfg = VARIANTS[filtered_name]

    # Pre-compute ledgers (same across all weights)
    ledgers = []
    for _regime, bars in days:
        strat = ScriptedStrategyV2(spec, cfg=variant_cfg)
        engine = Layer2Engine(spec, strat, seed=seed)
        engine._rejection_p = 0.0
        ledgers.append(engine.run(bars))

    # Pre-compute gauntlet day scores
    g_deltas = []
    for regime, bars in days:
        g_score = gauntlet_day_score(
            bars,
            regime=regime if regime != "unknown" else None,
        )
        g_deltas.append(g_score.delta)

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
        results.append(
            SweepResult(
                weight=weight,
                total_pnl=round(total_pnl, 2),
                n_trades=n_trades,
                n_full=n_full,
                n_reduced=n_reduced,
                n_skip=n_skip,
                avg_pnl_per_trade=round(avg, 2),
            )
        )
        print(
            f"  w={weight:.2f} → PnL=${total_pnl:+,.2f}, "
            f"trades={n_trades}, full={n_full}/red={n_reduced}/skip={n_skip}"
        )

    return results


def _render(results: list[SweepResult]) -> str:
    lines = ["# Gauntlet V16 Weight Sweep", ""]
    lines.append("Evaluates the impact of blending gauntlet delta into Apex V3 delta.")
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
    lines.append(
        f"**Best weight:** {best.weight:.2f} (PnL ${best.total_pnl:+,.2f}, {best.n_trades} trades)"
    )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    if best.weight == 0.0:
        lines.append(
            "Pure Apex (weight 0.00) was optimal. The gauntlet did not add "
            "value at any weight — gate thresholds may need tuning, or "
            "the sample is too small to show an edge."
        )
    elif best.weight <= 0.15:
        lines.append(
            f"Optimal weight {best.weight:.2f} suggests the gauntlet adds "
            f"a modest improvement when lightly blended. The 15-voice Apex "
            f"engine remains the primary signal."
        )
    else:
        lines.append(
            f"Optimal weight {best.weight:.2f} is higher than expected, "
            f"suggesting the gauntlet's binary filters are adding "
            f"significant signal beyond the continuous V1-V15 voices. "
            f"Validate on a larger sample before production use."
        )

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gauntlet V16 weight sweep.")
    parser.add_argument("--filtered", type=str, default="r5_real_wide_target")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    print("=== Gauntlet V16 weight sweep ===")
    results = _run_sweep(filtered_name=args.filtered, seed=args.seed)
    md = _render(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
