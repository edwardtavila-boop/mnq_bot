"""Phase B #14 — Hot-hand detector.

Detects whether running a win streak has positive or negative
autocorrelation in this journal. If P(win|win) > P(win|loss), there's
a real hot-hand signal and you might size up after wins. If it's
flat or reversed, you're chasing.

Writes ``reports/hot_hand.md`` with the conditional probabilities
and a recommendation.

Usage:
    python scripts/hot_hand_detector.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "hot_hand.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def main() -> int:
    argparse.ArgumentParser().parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if len(trades) < 4:
        REPORT_PATH.write_text("# Hot Hand\n\n_need ≥4 trades_\n")
        print("hot_hand: insufficient trades")
        return 0

    wins = [t.net_pnl > 0 for t in trades]
    # P(win | prev win), P(win | prev loss)
    tt = tf = ft = ff = 0
    for i in range(1, len(wins)):
        if wins[i - 1]:
            if wins[i]: tt += 1
            else: tf += 1
        else:
            if wins[i]: ft += 1
            else: ff += 1
    p_w_given_w = tt / (tt + tf) if (tt + tf) else 0
    p_w_given_l = ft / (ft + ff) if (ft + ff) else 0
    base_wr = sum(wins) / len(wins)

    signal = p_w_given_w - p_w_given_l
    label = (
        "🟢 HOT HAND — size up after wins"
        if signal > 0.10
        else "🔴 COLD REVERSION — stand down after wins"
        if signal < -0.10
        else "🟡 FLAT — streaks don't predict"
    )

    # Expected value of next trade after N-win streak (up to N=4)
    def _after_streak(n: int) -> tuple[int, float]:
        c, total = 0, 0.0
        for i in range(n, len(trades)):
            if all(wins[i - k - 1] for k in range(n)):
                total += trades[i].net_pnl
                c += 1
        return c, (total / c if c else 0.0)

    lines = [
        f"# Hot Hand · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades: **{len(trades)}** · base WR: **{base_wr:.1%}**",
        f"- P(win | prev win): **{p_w_given_w:.1%}**  (n={tt + tf})",
        f"- P(win | prev loss): **{p_w_given_l:.1%}**  (n={ft + ff})",
        f"- autocorrelation signal: **{signal:+.2%}**",
        f"- verdict: **{label}**",
        "",
        "## Expected next trade after N-win streak",
        "| Streak | N samples | Next-trade avg $ |",
        "|---:|---:|---:|",
    ]
    for n in range(1, 5):
        c, avg = _after_streak(n)
        if c:
            lines.append(f"| {n}-win | {c} | ${avg:+.2f} |")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"hot_hand: P(w|w)={p_w_given_w:.2%} · P(w|l)={p_w_given_l:.2%} · signal={signal:+.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
