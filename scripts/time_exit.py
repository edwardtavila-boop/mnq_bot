"""Phase A #10 — Time-based exit rule scanner.

Holds-time analysis: for each decile of hold-duration, what's the
expectancy? If trades kept past X minutes have negative expectancy,
we've found a candidate hard time-stop.

Writes ``reports/time_exit.md`` with the duration-bucketed table and
a recommended max-hold suggestion.

Usage:
    python scripts/time_exit.py
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "time_exit.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def main() -> int:
    argparse.ArgumentParser().parse_args()

    trades = [t for t in load_trades() if t.duration_s > 0]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# Time Exit\n\n_no trades with duration in journal_\n")
        print("time_exit: no trades")
        return 0

    sorted_t = sorted(trades, key=lambda t: t.duration_s)
    # Split into 5 duration quintiles
    n = len(sorted_t)
    bucket_size = max(1, n // 5)
    buckets = []
    for i in range(0, n, bucket_size):
        chunk = sorted_t[i : i + bucket_size]
        if not chunk:
            continue
        pnls = [c.net_pnl for c in chunk]
        rs = [c.r_multiple for c in chunk]
        wins = sum(1 for p in pnls if p > 0)
        buckets.append({
            "lo_s": chunk[0].duration_s,
            "hi_s": chunk[-1].duration_s,
            "n": len(chunk),
            "wr": wins / len(chunk),
            "exp": statistics.fmean(pnls),
            "avg_r": statistics.fmean(rs),
        })

    # Recommend time-stop: find the earliest bucket where expectancy turns negative
    recommended_cap: float | None = None
    for b in buckets:
        if b["exp"] < 0:
            recommended_cap = b["lo_s"]
            break

    lines = [
        f"# Time Exit · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades with duration: **{n}**",
        f"- median hold: **{statistics.median([t.duration_s for t in trades]):.0f}s**",
        f"- mean hold: **{statistics.fmean([t.duration_s for t in trades]):.0f}s**",
        "",
        "## Duration quintiles",
        "| Range (s) | N | WR | Exp $ | Avg R |",
        "|---|---:|---:|---:|---:|",
    ]
    for b in buckets:
        lines.append(
            f"| {b['lo_s']:.0f}–{b['hi_s']:.0f} | {b['n']} | "
            f"{b['wr']:.0%} | ${b['exp']:+.2f} | {b['avg_r']:+.2f} |"
        )

    lines += ["", "## Recommendation"]
    if recommended_cap is not None:
        lines.append(
            f"- Consider a **hard time-stop at ~{recommended_cap:.0f}s** — "
            f"trades held longer show negative expectancy."
        )
    else:
        lines.append("- No duration bucket is decisively negative — no time-stop needed yet.")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(
        f"time_exit: {n} trades · cap_suggestion="
        f"{'%.0fs' % recommended_cap if recommended_cap else 'none'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
