"""Phase A #02 — Rolling edge-decay watcher.

Compares the last-N-trade expectancy against the all-time expectancy.
Flags when the short window falls below a configurable fraction of the
long-run mean. Writes ``reports/edge_decay.md`` with the trend series.

Usage:
    python scripts/edge_decay.py
    python scripts/edge_decay.py --window 50 --threshold 0.6
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "edge_decay.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def rolling(values: list[float], window: int) -> list[float]:
    out = []
    for i in range(len(values)):
        lo = max(0, i - window + 1)
        sl = values[lo : i + 1]
        out.append(statistics.fmean(sl) if sl else 0.0)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--window", type=int, default=50)
    p.add_argument(
        "--threshold", type=float, default=0.6, help="Flag if rolling < all-time × threshold"
    )
    args = p.parse_args()

    trades = load_trades()
    if not trades:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("# Edge decay\n\n_no trades in journal_\n")
        print("edge_decay: no trades")
        return 0

    r_series = [t.r_multiple for t in trades]
    alltime = statistics.fmean(r_series)
    roll = rolling(r_series, args.window)
    last_window = roll[-1]
    ratio = (last_window / alltime) if alltime else 0.0
    decayed = alltime > 0 and last_window < alltime * args.threshold

    # Build a simple sparkline of the rolling series (last 40 pts).
    sl = roll[-40:] if len(roll) > 40 else roll
    sparkline_chars = " ▁▂▃▄▅▆▇█"
    if sl:
        lo, hi = min(sl), max(sl)
        if hi == lo:
            spark = "▄" * len(sl)
        else:
            spark = "".join(sparkline_chars[min(8, int((v - lo) / (hi - lo) * 8))] for v in sl)
    else:
        spark = ""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    status = "🟥 DECAYED" if decayed else ("🟨 WATCH" if ratio < 0.8 else "🟩 HEALTHY")
    flag_word = "FLAG" if decayed else "ok"
    lines = [
        f"# Edge decay · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades: **{len(trades)}**",
        f"- window: **{args.window}**",
        f"- all-time avg R: **{alltime:+.3f}**",
        f"- last-window avg R: **{last_window:+.3f}**",
        f"- ratio last/alltime: **{ratio:.2f}**",
        f"- threshold: {args.threshold}",
        f"- decision: **{status}**  (`{flag_word}`)",
        "",
        "## Rolling sparkline (last 40)",
        f"`{spark}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines))
    print(
        f"edge_decay: {status} · last={last_window:+.3f} R · alltime={alltime:+.3f} R · ratio={ratio:.2f}"
    )
    return 1 if decayed else 0


if __name__ == "__main__":
    sys.exit(main())
