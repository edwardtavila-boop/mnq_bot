"""Phase C #30 — Seasonality scanner.

Looks at what day-of-month, day-of-week, and hour-of-day have
historically been most productive in the journal, and overlays the
"statistical day" (e.g. Monday = mean-revert, Tuesday-Thursday =
trend, Friday = chop). Helps bias size per day.

Usage:
    python scripts/seasonality.py
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "seasonality.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


WEEKDAY_CANON = {
    0: "Mon · often mean-revert opening",
    1: "Tue · strongest trend day historically",
    2: "Wed · FOMC-risk midweek",
    3: "Thu · second-best trend day",
    4: "Fri · chop, position-unwinding",
}


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    trades = load_trades()
    if not trades:
        REPORT_PATH.write_text("# Seasonality\n\n_no trades_\n")
        print("seasonality: no trades")
        return 0

    by_wd: dict = defaultdict(list)
    by_hour: dict = defaultdict(list)
    by_dom: dict = defaultdict(list)
    for t in trades:
        if t.exit_ts:
            by_wd[t.exit_ts.weekday()].append(t.net_pnl)
            by_hour[t.exit_ts.hour].append(t.net_pnl)
            by_dom[t.exit_ts.day].append(t.net_pnl)

    lines = [
        f"# Seasonality · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "## By weekday (observed × canonical bias)",
        "| Weekday | N | Exp $ | Canonical bias |",
        "|---|---:|---:|---|",
    ]
    for wd in range(7):
        n = len(by_wd.get(wd, []))
        if n:
            e = statistics.fmean(by_wd[wd])
            lines.append(f"| {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][wd]} | {n} | ${e:+.2f} | {WEEKDAY_CANON.get(wd, '—')} |")

    lines += ["", "## By hour (UTC)", "| Hour | N | Exp $ |", "|---|---:|---:|"]
    for h in sorted(by_hour):
        n = len(by_hour[h])
        e = statistics.fmean(by_hour[h])
        lines.append(f"| H{h:02d} | {n} | ${e:+.2f} |")

    lines += ["", "## By day-of-month", "| DoM | N | Exp $ |", "|---|---:|---:|"]
    for d in sorted(by_dom):
        n = len(by_dom[d])
        e = statistics.fmean(by_dom[d])
        lines.append(f"| {d:02d} | {n} | ${e:+.2f} |")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"seasonality: weekdays={len(by_wd)} hours={len(by_hour)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
