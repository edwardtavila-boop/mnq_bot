"""Phase A #07 — Daily trade governor.

Enforces a hard cap on trades per day + a cool-off window after a loss
streak. Reads today's trades from the journal, computes the guard
state, and writes ``reports/trade_governor.md`` with a pass/hold
verdict that downstream automations can consume.

Usage:
    python scripts/trade_governor.py --max-trades 8 --loss-streak 3 --cooloff-min 30
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "trade_governor.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-trades", type=int, default=8)
    p.add_argument(
        "--loss-streak", type=int, default=3, help="Trigger cool-off after N consecutive losses"
    )
    p.add_argument("--cooloff-min", type=int, default=30)
    p.add_argument(
        "--max-daily-loss", type=float, default=-150.0, help="Hard USD daily loss cap (negative)"
    )
    p.add_argument(
        "--advisory",
        action="store_true",
        help=(
            "Advisory mode: compute the verdict and write the "
            "report as usual, but always return rc=0. Use for "
            "dev/replay runs where the journal contains synthetic "
            "fills that would trip the live-trading thresholds."
        ),
    )
    p.add_argument(
        "--strict-today",
        action="store_true",
        help=(
            "Restrict 'today' to the current UTC calendar date "
            "only — never fall back to the most recent trade's "
            "exit date. Use this in live cron to prevent stale "
            "journal entries from tripping governor thresholds."
        ),
    )
    args = p.parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# Trade Governor\n\n_no trades in journal_\n")
        print("trade_governor: no trades")
        return 0

    today = datetime.now(UTC).date()
    if args.strict_today:
        # Live-cron mode: only trades whose exit date == real UTC today count.
        last_day = today
    else:
        # Replay/dev mode: fall back to the most recent exit date so back-runs
        # of historical tape still exercise the governor logic.
        last_day = max((t.exit_ts.date() for t in trades if t.exit_ts), default=today)
    todays = [t for t in trades if t.exit_ts and t.exit_ts.date() == last_day]

    # Count running streak of losses at tail
    streak = 0
    for t in reversed(todays):
        if t.net_pnl < 0:
            streak += 1
        else:
            break

    daily_pnl = sum(t.net_pnl for t in todays)
    n_today = len(todays)

    verdict = "PASS"
    reasons = []
    next_ok_ts: datetime | None = None

    if n_today >= args.max_trades:
        verdict = "HOLD"
        reasons.append(f"trade cap hit ({n_today}/{args.max_trades})")

    if streak >= args.loss_streak:
        verdict = "HOLD"
        last_exit = todays[-1].exit_ts if todays else datetime.now(UTC)
        next_ok_ts = last_exit + timedelta(minutes=args.cooloff_min)
        reasons.append(f"{streak}-loss streak → cool-off until {next_ok_ts.strftime('%H:%M UTC')}")

    if daily_pnl <= args.max_daily_loss:
        verdict = "HOLD"
        reasons.append(f"daily loss cap hit (${daily_pnl:+.2f} ≤ ${args.max_daily_loss:+.2f})")

    status_icon = "🟢 PASS" if verdict == "PASS" else "🔴 HOLD"
    lines = [
        f"# Trade Governor · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- evaluation day: **{last_day.isoformat()}**",
        f"- trades today: **{n_today}** / cap {args.max_trades}",
        f"- daily PnL: **${daily_pnl:+.2f}** / floor ${args.max_daily_loss:+.2f}",
        f"- current loss streak: **{streak}** (triggers at {args.loss_streak})",
        f"- verdict: **{status_icon}**",
        "",
        "## Reasons",
    ]
    if reasons:
        lines.extend(f"- {r}" for r in reasons)
    else:
        lines.append("- all checks clean — continue trading")
    if next_ok_ts:
        lines += ["", f"Next-eligible entry: `{next_ok_ts.isoformat()}`"]

    if args.advisory and verdict == "HOLD":
        lines.insert(
            lines.index("## Reasons"),
            "- mode: **🟡 ADVISORY** (dev/replay — returning rc=0 regardless of verdict)",
        )

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    mode = " [advisory]" if args.advisory else ""
    print(f"trade_governor{mode}: {verdict} · n={n_today} pnl={daily_pnl:+.2f} streak={streak}")
    if args.advisory:
        return 0
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
