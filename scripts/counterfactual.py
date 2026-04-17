"""Phase A #05 — Counterfactual: what if we'd skipped the worst 10%?

Re-runs the equity curve with the bottom decile of trades removed and
reports the lift. Also computes "skip the first loss of each day" and
"skip after-loss trades" counterfactuals — three cheap filters to
pressure-test before committing to a formal governor.

Usage:
    python scripts/counterfactual.py
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "counterfactual.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades, summary_stats  # noqa: E402


def _equity_curve(pnl_series: list[float]) -> tuple[float, float]:
    """Return (final_equity, max_drawdown)."""
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnl_series:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return eq, max_dd


def main() -> int:
    argparse.ArgumentParser().parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# Counterfactual\n\n_no trades in journal_\n")
        print("counterfactual: no trades")
        return 0

    base_pnl = [t.net_pnl for t in trades]
    base_stats = summary_stats(trades)
    base_eq, base_dd = _equity_curve(base_pnl)

    # Counterfactual 1: skip worst decile
    threshold_pnl = sorted(base_pnl)[max(0, len(base_pnl) // 10)]
    skip_worst = [p for p in base_pnl if p > threshold_pnl]
    sw_eq, sw_dd = _equity_curve(skip_worst)

    # Counterfactual 2: skip first loss of each day
    by_day: dict[str, list] = {}
    for t in trades:
        if not t.exit_ts:
            continue
        by_day.setdefault(t.exit_ts.date().isoformat(), []).append(t)
    skip_first_loss = []
    for _, ts in by_day.items():
        first_loss_skipped = False
        for t in ts:
            if not first_loss_skipped and t.net_pnl < 0:
                first_loss_skipped = True
                continue
            skip_first_loss.append(t.net_pnl)
    sfl_eq, sfl_dd = _equity_curve(skip_first_loss)

    # Counterfactual 3: skip trade immediately after any loss
    skip_after_loss = []
    prev_was_loss = False
    for t in trades:
        if prev_was_loss:
            prev_was_loss = t.net_pnl < 0
            continue
        skip_after_loss.append(t.net_pnl)
        prev_was_loss = t.net_pnl < 0
    sal_eq, sal_dd = _equity_curve(skip_after_loss)

    def _exp(xs: list[float]) -> float:
        return statistics.fmean(xs) if xs else 0.0

    lines = [
        f"# Counterfactual · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- baseline: n={base_stats['n']} · total=${base_eq:+.2f} · maxDD=${base_dd:.2f}",
        "",
        "| Scenario | N | Total | Exp / trade | MaxDD | Lift vs base |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Baseline | {len(base_pnl)} | ${base_eq:+.2f} | ${_exp(base_pnl):+.2f} | ${base_dd:.2f} | — |",
        f"| Skip worst decile | {len(skip_worst)} | ${sw_eq:+.2f} | ${_exp(skip_worst):+.2f} | ${sw_dd:.2f} | ${sw_eq - base_eq:+.2f} |",
        f"| Skip first loss/day | {len(skip_first_loss)} | ${sfl_eq:+.2f} | ${_exp(skip_first_loss):+.2f} | ${sfl_dd:.2f} | ${sfl_eq - base_eq:+.2f} |",
        f"| Skip after-loss | {len(skip_after_loss)} | ${sal_eq:+.2f} | ${_exp(skip_after_loss):+.2f} | ${sal_dd:.2f} | ${sal_eq - base_eq:+.2f} |",
        "",
        "## Interpretation",
        "- A big jump from *skip worst decile* usually means size discipline, not setup choice.",
        "- A win from *skip first loss/day* hints at morning chop — delay entry window.",
        "- A win from *skip after-loss* suggests mean-reversion in streaks — confirm with streak analyzer.",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"counterfactual: baseline={base_eq:+.2f} · skipWorst={sw_eq:+.2f} · skipFirstLoss={sfl_eq:+.2f} · skipAfterLoss={sal_eq:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
