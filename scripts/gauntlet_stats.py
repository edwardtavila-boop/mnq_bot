"""Gauntlet statistics — A/B comparison of gated vs ungated shadow trading.

Batch 5A. Runs shadow_trader twice (with and without the 12-gate gauntlet
pre-filter) and produces a side-by-side comparison report showing:

  * PnL impact (how much does the gauntlet cost / save?)
  * Trade frequency impact (how many trades does the gauntlet block?)
  * Per-gate failure distribution (which gates fire most often?)
  * Win-rate shift (does the gauntlet improve selectivity?)

Output: ``reports/gauntlet_stats.md``

Usage:
    python scripts/gauntlet_stats.py
    python scripts/gauntlet_stats.py --filtered r5_real_wide_target
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from shadow_trader import (  # noqa: E402
    DaySummary,
    GauntletTradeResult,
    run_shadow,
)

DEFAULT_REPORT = REPO_ROOT / "reports" / "gauntlet_stats.md"
JOURNAL_UNGATED = REPO_ROOT / "data" / "shadow" / "fills_ungated.jsonl"
JOURNAL_GATED = REPO_ROOT / "data" / "shadow" / "fills_gated.jsonl"


def _run_ab(*, filtered_name: str, seed: int) -> dict:
    """Run shadow twice: ungated and gated."""
    print("=== Ungated run (gauntlet OFF) ===")
    ungated = run_shadow(
        filtered_name=filtered_name,
        journal_path=JOURNAL_UNGATED,
        truncate_journal=True,
        seed=seed,
        use_gauntlet=False,
    )
    print(f"   PnL: ${ungated['total_shadow_pnl']:+,.2f}  fills: {ungated['total_shadow_fills']}")

    print("=== Gated run (gauntlet ON) ===")
    gated = run_shadow(
        filtered_name=filtered_name,
        journal_path=JOURNAL_GATED,
        truncate_journal=True,
        seed=seed,
        use_gauntlet=True,
    )
    print(f"   PnL: ${gated['total_shadow_pnl']:+,.2f}  fills: {gated['total_shadow_fills']}")

    return {"ungated": ungated, "gated": gated}


def _render_comparison(ab: dict) -> str:
    ungated = ab["ungated"]
    gated = ab["gated"]
    lines: list[str] = ["# Gauntlet A/B Comparison", ""]

    # -- Summary stats --
    ug_pnl = ungated["total_shadow_pnl"]
    g_pnl = gated["total_shadow_pnl"]
    ug_fills = ungated["total_shadow_fills"]
    g_fills = gated["total_shadow_fills"]
    ug_trades = ug_fills // 2
    g_trades = g_fills // 2
    blocked = ug_trades - g_trades
    pnl_delta = g_pnl - ug_pnl

    lines.append(f"- Filtered variant: `{ungated['filtered_name']}`")
    lines.append(f"- Days: **{ungated['n_days']}**")
    lines.append("")

    lines.append("## Head-to-head")
    lines.append("")
    lines.append("| Metric | Ungated | Gated | Δ |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| Shadow PnL | ${ug_pnl:+,.2f} | ${g_pnl:+,.2f} | ${pnl_delta:+,.2f} |")
    lines.append(f"| Trades | {ug_trades} | {g_trades} | {-blocked} |")
    lines.append(f"| Fills | {ug_fills} | {g_fills} | {g_fills - ug_fills} |")

    if ug_trades > 0:
        ug_avg = ug_pnl / ug_trades
        g_avg = g_pnl / g_trades if g_trades > 0 else 0.0
        lines.append(
            f"| Avg PnL/trade | ${ug_avg:+,.2f} | ${g_avg:+,.2f} | ${g_avg - ug_avg:+,.2f} |"
        )

    block_rate = blocked / ug_trades * 100 if ug_trades else 0.0
    lines.append(f"| Block rate | — | {block_rate:.1f}% | — |")
    lines.append("")

    # -- Per-gate failure distribution --
    gauntlet_results: list[GauntletTradeResult] = gated.get("gauntlet_results", [])
    if gauntlet_results:
        gate_fail_counts: dict[str, int] = {}
        total_failures = 0
        for gr in gauntlet_results:
            for g in gr.failed_gates:
                gate_fail_counts[g] = gate_fail_counts.get(g, 0) + 1
                total_failures += 1
        sorted_gates = sorted(gate_fail_counts.items(), key=lambda x: -x[1])

        lines.append("## Gate failure distribution")
        lines.append("")
        lines.append("| Gate | Failures | % of total |")
        lines.append("|---|---:|---:|")
        for gate_name, count in sorted_gates:
            pct = count / total_failures * 100 if total_failures else 0
            lines.append(f"| {gate_name} | {count} | {pct:.1f}% |")
        lines.append("")

    # -- Per-day comparison --
    ug_summaries: list[DaySummary] = ungated["summaries"]
    g_summaries: list[DaySummary] = gated["summaries"]

    lines.append("## Per-day comparison")
    lines.append("")
    lines.append("| Day | Regime | Ungated PnL | Gated PnL | Δ PnL | G✓ | G✗ |")
    lines.append("|---:|---|---:|---:|---:|---:|---:|")
    for ug_s, g_s in zip(ug_summaries, g_summaries, strict=True):
        d_pnl = g_s.shadow_pnl - ug_s.shadow_pnl
        lines.append(
            f"| {ug_s.day_idx} | {ug_s.regime} | "
            f"${ug_s.shadow_pnl:+,.2f} | ${g_s.shadow_pnl:+,.2f} | "
            f"${d_pnl:+,.2f} | {g_s.gauntlet_passed} | {g_s.gauntlet_blocked} |"
        )

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    if pnl_delta > 0:
        lines.append(
            f"The gauntlet **improved** PnL by ${pnl_delta:+,.2f} while blocking "
            f"{blocked} trades ({block_rate:.1f}% block rate). The filter is additive."
        )
    elif pnl_delta < 0:
        lines.append(
            f"The gauntlet **reduced** PnL by ${pnl_delta:+,.2f} while blocking "
            f"{blocked} trades ({block_rate:.1f}% block rate). Some blocked trades "
            f"were winners — review gate thresholds for over-filtering."
        )
    else:
        lines.append(
            f"The gauntlet was **neutral** on PnL while blocking "
            f"{blocked} trades ({block_rate:.1f}% block rate)."
        )

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gauntlet A/B comparison.")
    parser.add_argument("--filtered", type=str, default="r5_real_wide_target")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    ab = _run_ab(filtered_name=args.filtered, seed=args.seed)
    md = _render_comparison(ab)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
