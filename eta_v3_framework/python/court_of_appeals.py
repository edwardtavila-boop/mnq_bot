"""
Apex v2 Court of Appeals
========================
Weekly trade replay per the Firm spec. Loads the last week of trades from
the webhook log, reranks by PM confidence, picks the 5 most and least
confident, re-evaluates outcomes vs predictions, generates a report.

Use it as a Friday post-mortem: did the system fire the right trades for
the right reasons? Are the high-PM trades actually winning more than the
low-PM trades? If not, the PM threshold needs recalibration.

Usage:
  python court_of_appeals.py [--log logs/trades.jsonl] [--days 7] [--out report.md]
"""

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path


def load_trades(log_path: Path, days: int) -> list[dict]:
    if not log_path.exists():
        return []
    cutoff = datetime.now(UTC) - timedelta(days=days)
    trades = []
    with log_path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            recv_str = rec.get("received_at", "")
            try:
                recv = datetime.fromisoformat(recv_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if recv < cutoff:
                continue
            # Only include actually-fired trades that have outcomes attached
            if rec.get("validation", {}).get("server_validation") != "passed":
                continue
            trades.append(rec)
    return trades


def attach_outcomes(trades: list[dict]) -> list[dict]:
    """If outcomes aren't logged yet, mark as 'pending'.
    In production, you'd join with broker fills; for now we use the outcome
    field if it exists in the log, else 'pending'."""
    for t in trades:
        broker = t.get("broker", {})
        outcome = broker.get("outcome", t.get("payload", {}).get("outcome", "pending"))
        t["outcome"] = outcome
        # Convert outcome string to estimated R if possible
        if outcome == "tp1":
            t["pnl_r"] = 1.0
        elif outcome == "tp2":
            t["pnl_r"] = 2.0
        elif outcome == "sl":
            t["pnl_r"] = -1.0
        elif outcome == "be":
            t["pnl_r"] = 0.0
        else:
            t["pnl_r"] = None
    return trades


def section_header(title: str) -> str:
    return f"\n## {title}\n\n"


def format_trade_row(t: dict) -> str:
    p = t["payload"]
    recv = datetime.fromisoformat(t["received_at"].replace("Z", "+00:00"))
    pnl = t.get("pnl_r")
    pnl_str = f"{pnl:+.2f}R" if pnl is not None else "pending"
    voices_str = ",".join(str(int(v)) for v in p["voices"])
    return (
        f"| {recv.strftime('%a %m/%d %H:%M')} | {p['side']:5s} | {p['setup']:6s} | "
        f"{p['regime']:8s} | {p['pm_final']:5.0f} | {p['red_team']:4.0f} | "
        f"`[{voices_str}]` | {t.get('outcome', '—'):>8s} | {pnl_str:>8s} |"
    )


def build_report(trades: list[dict], days: int) -> str:
    out = []
    out.append("# The Firm · Court of Appeals\n")
    out.append(
        f"_Trade replay for the last {days} days. "
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}._\n"
    )

    if not trades:
        out.append("\n**No trades fired in this period.**\n")
        return "".join(out)

    # ── Overview ──
    out.append(section_header("Overview"))
    n = len(trades)
    with_pnl = [t for t in trades if t.get("pnl_r") is not None]
    n_pending = n - len(with_pnl)
    wins = [t for t in with_pnl if t["pnl_r"] > 0]
    losses = [t for t in with_pnl if t["pnl_r"] < 0]
    total_r = sum(t["pnl_r"] for t in with_pnl)
    win_rate = len(wins) / len(with_pnl) * 100 if with_pnl else 0
    avg_pm = sum(t["payload"]["pm_final"] for t in trades) / n
    avg_red = sum(t["payload"]["red_team"] for t in trades) / n

    out.append(f"- **Trades:** {n}  ({len(with_pnl)} resolved, {n_pending} pending)\n")
    out.append(f"- **Win rate (resolved):** {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)\n")
    out.append(f"- **Total R:** {total_r:+.2f}\n")
    out.append(f"- **Avg PM final:** {avg_pm:.1f}\n")
    out.append(f"- **Avg Red Team:** {avg_red:.1f}\n")

    # ── Most confident 5 ──
    sorted_by_pm = sorted(trades, key=lambda x: -x["payload"]["pm_final"])
    top5 = sorted_by_pm[:5]
    bottom5 = sorted_by_pm[-5:]

    out.append(section_header("Top 5 most confident (highest PM)"))
    out.append("| Time | Side | Setup | Regime | PM | Red | Voices | Outcome | PnL |\n")
    out.append("|------|------|-------|--------|----|----|--------|---------|-----|\n")
    for t in top5:
        out.append(format_trade_row(t) + "\n")

    out.append(section_header("Bottom 5 least confident (lowest PM that still fired)"))
    out.append("| Time | Side | Setup | Regime | PM | Red | Voices | Outcome | PnL |\n")
    out.append("|------|------|-------|--------|----|----|--------|---------|-----|\n")
    for t in bottom5:
        out.append(format_trade_row(t) + "\n")

    # ── Calibration check: do high-PM trades win more? ──
    out.append(section_header("PM calibration (does higher confidence = more wins?)"))
    if len(with_pnl) >= 4:
        sorted_resolved = sorted(with_pnl, key=lambda x: x["payload"]["pm_final"])
        half = len(sorted_resolved) // 2
        low_half = sorted_resolved[:half]
        high_half = sorted_resolved[half:]
        low_wr = sum(1 for t in low_half if t["pnl_r"] > 0) / len(low_half) * 100
        high_wr = sum(1 for t in high_half if t["pnl_r"] > 0) / len(high_half) * 100
        low_r = sum(t["pnl_r"] for t in low_half) / len(low_half)
        high_r = sum(t["pnl_r"] for t in high_half) / len(high_half)
        out.append(f"- **Lower-half PM trades:** win rate {low_wr:.1f}%, avg {low_r:+.2f}R\n")
        out.append(f"- **Upper-half PM trades:** win rate {high_wr:.1f}%, avg {high_r:+.2f}R\n")
        if high_wr > low_wr:
            out.append("- ✓ **Calibration holds:** higher PM correctly predicts more wins.\n")
        else:
            out.append(
                "- ✗ **CALIBRATION FAILURE:** higher PM does NOT predict more wins. "
                "Investigate voice weights and Red Team scoring.\n"
            )
    else:
        out.append("_Need at least 4 resolved trades for calibration check._\n")

    # ── Per-setup breakdown ──
    out.append(section_header("By setup"))
    by_setup = defaultdict(list)
    for t in trades:
        by_setup[t["payload"]["setup"]].append(t)
    out.append("| Setup | Fired | Resolved | Win% | Total R | Avg PM | Avg Red |\n")
    out.append("|-------|-------|----------|------|---------|--------|---------|\n")
    for setup, ts in sorted(by_setup.items()):
        resolved = [t for t in ts if t.get("pnl_r") is not None]
        wins_s = sum(1 for t in resolved if t["pnl_r"] > 0)
        total_r_s = sum(t["pnl_r"] for t in resolved)
        wr_s = wins_s / len(resolved) * 100 if resolved else 0
        avg_pm_s = sum(t["payload"]["pm_final"] for t in ts) / len(ts)
        avg_red_s = sum(t["payload"]["red_team"] for t in ts) / len(ts)
        out.append(
            f"| {setup} | {len(ts)} | {len(resolved)} | "
            f"{wr_s:.0f}% | {total_r_s:+.2f} | {avg_pm_s:.0f} | {avg_red_s:.0f} |\n"
        )

    # ── Per-regime breakdown ──
    out.append(section_header("By regime"))
    by_regime = defaultdict(list)
    for t in trades:
        by_regime[t["payload"]["regime"]].append(t)
    out.append("| Regime | Fired | Resolved | Win% | Total R |\n")
    out.append("|--------|-------|----------|------|---------|\n")
    for reg, ts in sorted(by_regime.items()):
        resolved = [t for t in ts if t.get("pnl_r") is not None]
        wins_r = sum(1 for t in resolved if t["pnl_r"] > 0)
        total_r_r = sum(t["pnl_r"] for t in resolved)
        wr_r = wins_r / len(resolved) * 100 if resolved else 0
        out.append(f"| {reg} | {len(ts)} | {len(resolved)} | {wr_r:.0f}% | {total_r_r:+.2f} |\n")

    # ── Verdict ──
    out.append(section_header("Verdict"))
    if win_rate >= 60 and total_r > 0:
        out.append("✓ **The Firm performed.** Continue current configuration.\n")
    elif win_rate >= 50 and total_r > 0:
        out.append("◉ **Marginal week.** Watch for drift over next 5 trading days.\n")
    else:
        out.append("✗ **The Firm underperformed.** Convene the desk:\n")
        out.append("  - Review the bottom-5 trades — were any obviously bad?\n")
        out.append("  - Check if a single regime accounts for most losses\n")
        out.append("  - Consider raising PM threshold by 5 points if this persists\n")

    return "".join(out)


def main():
    parser = argparse.ArgumentParser(description="Apex v2 Court of Appeals")
    parser.add_argument(
        "--log",
        default="logs/trades.jsonl",
        help="Path to trades log (default ./logs/trades.jsonl)",
    )
    parser.add_argument("--days", type=int, default=7, help="Look-back window in days (default 7)")
    parser.add_argument("--out", default="court_of_appeals.md", help="Output report path")
    args = parser.parse_args()

    log_path = Path(args.log)
    print(f"Loading trades from {log_path.absolute()}...")
    trades = load_trades(log_path, args.days)
    print(f"Found {len(trades)} fired trades in last {args.days} days")

    trades = attach_outcomes(trades)
    report = build_report(trades, args.days)

    out_path = Path(args.out)
    out_path.write_text(report)
    print(f"\nReport written to {out_path.absolute()}")
    print("\n" + "=" * 60)
    print(report)


if __name__ == "__main__":
    main()
