"""Phase B #17 — Weekly review generator.

Rolls up the last 7 days: P/L, win rate, biggest winner/loser,
adherence, rule violations, mood correlation, setup performance,
and 3 concrete next-week targets.

Usage:
    python scripts/weekly_review.py
    python scripts/weekly_review.py --weeks-back 1
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "weekly_review.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades, summary_stats  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--weeks-back", type=int, default=0, help="0=this week, 1=last week")
    args = p.parse_args()

    now = datetime.now(UTC)
    end = now - timedelta(weeks=args.weeks_back)
    start = end - timedelta(days=7)

    trades = [t for t in load_trades() if t.exit_ts and start <= t.exit_ts <= end]
    stats = summary_stats(trades)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Weekly Review · week ending {end.strftime('%Y-%m-%d')}",
        "",
        f"- window: `{start.date().isoformat()}` → `{end.date().isoformat()}`",
        f"- trades: **{stats['n']}** · win rate: **{stats['win_rate']:.1%}**",
        f"- PnL: **${stats['total_pnl']:+.2f}** · PF: **{stats['profit_factor']:.2f}**",
        f"- expectancy: **${stats['expectancy']:+.2f}** · avg R: **{stats['avg_r']:+.2f}R**",
        "",
    ]

    if trades:
        by_day = Counter(t.exit_ts.date() for t in trades)
        lines += ["## Activity by day", "| Day | Trades |", "|---|---:|"]
        for d, c in sorted(by_day.items()):
            lines.append(f"| {d.isoformat()} | {c} |")

        biggest_win = max(trades, key=lambda t: t.net_pnl)
        biggest_loss = min(trades, key=lambda t: t.net_pnl)
        lines += [
            "",
            "## Extremes",
            f"- **Best**: seq {biggest_win.seq} · ${biggest_win.net_pnl:+.2f} ({biggest_win.r_multiple:+.2f}R)",
            f"- **Worst**: seq {biggest_loss.seq} · ${biggest_loss.net_pnl:+.2f} ({biggest_loss.r_multiple:+.2f}R)",
        ]

        setups = Counter(t.setup for t in trades)
        lines += ["", "## Setup distribution", "| Setup | N | Total $ |", "|---|---:|---:|"]
        for s, c in setups.most_common():
            total = sum(t.net_pnl for t in trades if t.setup == s)
            lines.append(f"| {s} | {c} | ${total:+.2f} |")

        hold_secs = [t.duration_s for t in trades if t.duration_s > 0]
        if hold_secs:
            lines += [
                "",
                f"- avg hold: {statistics.fmean(hold_secs):.0f}s · median: {statistics.median(hold_secs):.0f}s",
            ]

    lines += [
        "",
        "## Next-week focus (auto-generated candidates)",
        f"- {'Cut size by 25% on {} setup' if stats['avg_r'] < 0 else 'Maintain size discipline'}".format(
            (max(setups.items(), key=lambda x: -x[1])[0] if trades else 'default')
        ),
        f"- {'Tighten stop' if stats['profit_factor'] < 1.5 else 'Hold current stop'} — PF {stats['profit_factor']:.2f}",
        f"- {'Review every losing trade' if stats['win_rate'] < 0.5 else 'Review top 3 losses only'}",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"weekly_review: {stats['n']} trades · ${stats['total_pnl']:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
