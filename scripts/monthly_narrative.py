"""Phase B #18 — Monthly narrative.

Rolls the full month into a story: what happened, what worked, what
didn't, what we'd change. Reads weekly_review summaries if present,
otherwise builds the narrative straight from the journal.

Usage:
    python scripts/monthly_narrative.py
    python scripts/monthly_narrative.py --month 2026-03
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "monthly_narrative.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades, summary_stats  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--month", default=datetime.now(UTC).strftime("%Y-%m"))
    args = p.parse_args()

    target = args.month  # YYYY-MM
    trades = [t for t in load_trades() if t.exit_ts and t.exit_ts.strftime("%Y-%m") == target]
    stats = summary_stats(trades)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text(f"# Monthly Narrative · {target}\n\n_no trades in this month_\n")
        print(f"monthly_narrative: no trades for {target}")
        return 0

    # Trend arc
    pnl_series = [t.net_pnl for t in trades]
    cum = []
    running = 0.0
    for p_ in pnl_series:
        running += p_
        cum.append(running)
    peak = max(cum)
    trough = min(cum)
    arc = (
        "steady compounding"
        if pnl_series[-1] > 0 and cum[-1] > peak * 0.8
        else "drawdown recovery"
        if trough < cum[-1] < 0
        else "choppy"
        if len(cum) > 1 and abs(statistics.stdev(cum)) > abs(cum[-1])
        else "flat"
    )

    # Theme extraction
    avg_hold = statistics.fmean([t.duration_s for t in trades if t.duration_s > 0]) if trades else 0
    biggest_day_pnl = float("-inf")
    worst_day_pnl = float("inf")
    from collections import defaultdict

    day_pnl: dict = defaultdict(float)
    for t in trades:
        if t.exit_ts:
            day_pnl[t.exit_ts.date()] += t.net_pnl
    if day_pnl:
        best_day = max(day_pnl.items(), key=lambda x: x[1])
        worst_day = min(day_pnl.items(), key=lambda x: x[1])
        biggest_day_pnl = best_day[1]
        worst_day_pnl = worst_day[1]

    lines = [
        f"# Monthly Narrative · {target}",
        "",
        f"The Firm traded **{stats['n']} times** in {target}, closing "
        f"**${stats['total_pnl']:+,.2f}** with a **{stats['win_rate']:.1%}** hit rate "
        f"and a **{stats['profit_factor']:.2f}** profit factor.",
        "",
        f"The equity arc was **{arc}** — peak cumulative PnL of ${peak:+,.2f} vs trough ${trough:+,.2f}. "
        f"Average hold was {avg_hold:.0f}s; the best single day added ${biggest_day_pnl:+,.2f}, "
        f"the worst cost ${worst_day_pnl:+,.2f}.",
        "",
        "## What worked",
        f"- avg R per trade: **{stats['avg_r']:+.2f}R** ({'positive edge held' if stats['avg_r'] > 0 else 'edge degraded'})",
        f"- avg win: **${stats['avg_win']:+.2f}** vs avg loss: **${stats['avg_loss']:+.2f}** — "
        f"{'payoff asymmetry in our favor' if stats['avg_win'] > abs(stats['avg_loss']) else 'losses are bigger than wins'}",
        "",
        "## What didn't",
        f"- {stats['losses']} losing trades took ${stats['avg_loss'] * stats['losses']:+,.2f} off the top line",
        f"- {'Drawdown exceeded single-trade risk — review sizing discipline' if trough < stats['avg_loss'] * 3 else 'Drawdowns stayed contained within expected R bands'}",
        "",
        "## One change for next month",
        f"- {'Tighten stops by 20% — avg loss is bigger than avg win' if abs(stats['avg_loss']) > stats['avg_win'] else 'Size up winners by 50% — payoff asymmetry supports it'}",
        "",
        "_Auto-narrated. Edit to add human context before archiving to journal._",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"monthly_narrative: {target} · {stats['n']} trades · ${stats['total_pnl']:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
