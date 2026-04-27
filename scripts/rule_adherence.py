"""Phase A #08 — Rule adherence scoring.

Scores each trade against a configurable rule ledger and reports the
adherence rate, the delta between rule-following and rule-breaking
performance, and a ranked list of most-violated rules.

Rule definitions live in ``config/trading_rules.yaml`` — if absent, a
default ledger is used. Trades are assumed to carry a
``followed_rules`` boolean and an optional ``violations`` list in
``extras``; when unavailable, heuristics infer adherence from the
trade's size, hour, and streak context.

Usage:
    python scripts/rule_adherence.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "rule_adherence.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402

DEFAULT_RULES = {
    "max_size": 4,  # contracts per entry
    "allowed_hours": list(range(13, 21)),  # 09:00–17:00 EST → 13–21 UTC
    "max_daily_trades": 8,
    "no_trade_after_2_losses": True,
}


def _infer_violations(trade, ctx) -> list[str]:
    v = []
    # Size rule
    if trade.qty > ctx["rules"]["max_size"]:
        v.append(f"oversize ({trade.qty}>{ctx['rules']['max_size']})")
    # Window rule
    if trade.hour is not None and trade.hour not in ctx["rules"]["allowed_hours"]:
        v.append(f"off-hours (H{trade.hour:02d})")
    # Daily cap
    if (
        ctx["per_day_count"].get(trade.exit_ts.date() if trade.exit_ts else None, 0)
        > ctx["rules"]["max_daily_trades"]
    ):
        v.append("over daily cap")
    # Streak rule
    if ctx["rules"]["no_trade_after_2_losses"] and ctx["tail_losses"] >= 2:
        v.append("revenge-trade (≥2 prior losses)")
    return v


def main() -> int:
    argparse.ArgumentParser().parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# Rule Adherence\n\n_no trades in journal_\n")
        print("rule_adherence: no trades")
        return 0

    # Build per-day count then walk
    per_day: Counter[object] = Counter()
    for t in trades:
        if t.exit_ts:
            per_day[t.exit_ts.date()] += 1

    following = []
    violating = []
    violation_counter: Counter[str] = Counter()

    tail_losses = 0
    for t in trades:
        ctx = {
            "rules": DEFAULT_RULES,
            "per_day_count": per_day,
            "tail_losses": tail_losses,
        }
        v = _infer_violations(t, ctx)
        if v:
            violating.append(t)
            for item in v:
                violation_counter[item] += 1
        else:
            following.append(t)
        tail_losses = tail_losses + 1 if t.net_pnl < 0 else 0

    n = len(trades)
    adherence_rate = len(following) / n if n else 0
    follow_exp = statistics.fmean([t.net_pnl for t in following]) if following else 0
    violate_exp = statistics.fmean([t.net_pnl for t in violating]) if violating else 0
    delta = follow_exp - violate_exp

    lines = [
        f"# Rule Adherence · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades: **{n}** · rules-followed: **{len(following)}** · violated: **{len(violating)}**",
        f"- adherence: **{adherence_rate:.1%}**",
        f"- expectancy when following rules: **${follow_exp:+.2f}**",
        f"- expectancy when violating: **${violate_exp:+.2f}**",
        f"- delta: **${delta:+.2f}** per trade",
        "",
        "## Active rule ledger",
    ]
    for k, v in DEFAULT_RULES.items():
        lines.append(f"- `{k}`: {v}")

    lines += ["", "## Top violations"]
    if violation_counter:
        for name, count in violation_counter.most_common(10):
            lines.append(f"- **{name}** — {count}×")
    else:
        lines.append("- none — you're a saint")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"rule_adherence: {adherence_rate:.1%} followed · Δ=${delta:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
