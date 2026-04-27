"""Phase A #04 — Hour × Day-of-week hit-rate heatmap.

Groups trades by exit hour (UTC) and weekday, writes a markdown table
where each cell shows `n | WR% | avg R`. Green for edge ≥ 1R and hit
rate ≥ 60%, amber for mixed, red for negative.

Usage:
    python scripts/time_heatmap.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "time_heatmap.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _cell_marker(wr: float, avg_r: float) -> str:
    if wr >= 0.6 and avg_r >= 1.0:
        return "🟢"
    if wr <= 0.4 or avg_r <= 0.0:
        return "🔴"
    return "🟡"


def main() -> int:
    argparse.ArgumentParser().parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# Time Heatmap\n\n_no trades in journal_\n")
        print("time_heatmap: no trades")
        return 0

    cells: dict[tuple[int, int], list[float]] = defaultdict(list)
    for t in trades:
        if t.hour is not None and t.weekday is not None:
            cells[(t.weekday, t.hour)].append(t.r_multiple)

    hours_present = sorted({h for _, h in cells})
    weekdays_present = sorted({w for w, _ in cells})

    # Build the main table
    lines = [
        f"# Time Heatmap · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades: **{len(trades)}** across {len(cells)} active (weekday, hour) buckets",
        "",
        "## Hit-rate by Weekday × Hour (UTC)",
        "",
        "| " + "Day" + " | " + " | ".join(f"H{h:02d}" for h in hours_present) + " |",
        "|" + "---|" * (len(hours_present) + 1),
    ]
    for w in weekdays_present:
        row = [WEEKDAYS[w]]
        for h in hours_present:
            rs = cells.get((w, h), [])
            if rs:
                wins = sum(1 for r in rs if r > 0)
                wr = wins / len(rs)
                avg_r = statistics.fmean(rs)
                row.append(f"{_cell_marker(wr, avg_r)} {len(rs)} · {wr:.0%} · {avg_r:+.1f}R")
            else:
                row.append("·")
        lines.append("| " + " | ".join(row) + " |")

    # Rank best / worst
    cell_scores = []
    for (w, h), rs in cells.items():
        if len(rs) >= 2:
            cell_scores.append(
                (
                    WEEKDAYS[w],
                    h,
                    len(rs),
                    sum(1 for r in rs if r > 0) / len(rs),
                    statistics.fmean(rs),
                )
            )
    cell_scores.sort(key=lambda row: row[-1], reverse=True)
    lines += [
        "",
        "## Best cells (min 2 trades, sorted by avg R)",
        "",
        "| Day | Hour | N | WR | Avg R |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in cell_scores[:5]:
        lines.append(f"| {row[0]} | {row[1]:02d} | {row[2]} | {row[3]:.0%} | {row[4]:+.2f} |")
    if len(cell_scores) > 5:
        lines += [
            "",
            "## Worst cells",
            "",
            "| Day | Hour | N | WR | Avg R |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in cell_scores[-5:]:
            lines.append(f"| {row[0]} | {row[1]:02d} | {row[2]} | {row[3]:.0%} | {row[4]:+.2f} |")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"time_heatmap: {len(cells)} buckets populated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
