"""Phase B #13 — Loss streak monitor.

Walks the journal, identifies loss-streak segments, computes average
streak length, deepest drawdown streak, and whether the active tail
is currently building a streak. Emits a state file that
``pre_trade_pause`` can consume to auto-set HOT.

Usage:
    python scripts/loss_streak_monitor.py --max-streak 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "loss_streak.md"
STATE_PATH = REPO_ROOT / "data" / "loss_streak_state.json"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--max-streak", type=int, default=3, help="Trigger warning at this streak length"
    )
    args = p.parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# Loss Streak\n\n_no trades in journal_\n")
        print("loss_streak: no trades")
        return 0

    # Scan all streaks
    streaks = []
    cur = 0
    cur_depth = 0.0
    for t in trades:
        if t.net_pnl < 0:
            cur += 1
            cur_depth += t.net_pnl
        else:
            if cur > 0:
                streaks.append((cur, cur_depth))
            cur = 0
            cur_depth = 0
    tail_streak = cur
    tail_depth = cur_depth
    if cur > 0:
        streaks.append((cur, cur_depth))

    worst_streak = max(streaks, key=lambda x: x[0]) if streaks else (0, 0)
    deepest = min(streaks, key=lambda x: x[1]) if streaks else (0, 0)
    avg_len = statistics.fmean([s[0] for s in streaks]) if streaks else 0

    triggered = tail_streak >= args.max_streak
    state = {
        "tail_streak": tail_streak,
        "tail_depth": tail_depth,
        "triggered": triggered,
        "threshold": args.max_streak,
        "ts": datetime.now(UTC).isoformat(),
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))

    icon = "🔴 TRIGGERED" if triggered else "🟢 nominal"
    lines = [
        f"# Loss Streak Monitor · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades scanned: **{len(trades)}**",
        f"- active tail streak: **{tail_streak}** (depth ${tail_depth:+.2f})",
        f"- threshold: **{args.max_streak}** → status: **{icon}**",
        f"- total losing streaks observed: **{len(streaks)}**",
        f"- avg streak length: **{avg_len:.2f}**",
        f"- worst (longest): **{worst_streak[0]}** trades · ${worst_streak[1]:+.2f}",
        f"- deepest drawdown streak: **{deepest[0]}** trades · ${deepest[1]:+.2f}",
        "",
        "## Histogram (streak length → count)",
    ]
    from collections import Counter

    hist = Counter(s[0] for s in streaks)
    for k in sorted(hist):
        lines.append(f"- {k}: {'█' * hist[k]} ({hist[k]})")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"loss_streak: tail={tail_streak} · triggered={triggered}")
    return 1 if triggered else 0


if __name__ == "__main__":
    sys.exit(main())
