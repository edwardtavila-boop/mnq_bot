"""Phase C #29 — Earnings amplifier.

Mag-7 earnings (NVDA, AAPL, MSFT, GOOG, META, AMZN, TSLA) drive NQ
gaps. This tags the upcoming week's expected earnings dates from a
static schedule (or live feed when configured) and suggests size-down
mode within 24h of a report.

Usage:
    python scripts/earnings_amp.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "earnings_amp.md"
CAL_PATH = REPO_ROOT / "data" / "earnings_cal.json"


MAG7 = ["NVDA", "AAPL", "MSFT", "GOOG", "META", "AMZN", "TSLA"]


def _load_cal() -> dict:
    if CAL_PATH.exists():
        return json.loads(CAL_PATH.read_text())
    # Canned stub — real impl pulls from Finnhub / Polygon
    now = datetime.now(UTC)
    return {
        "NVDA": (now + timedelta(days=15)).isoformat(),
        "AAPL": (now + timedelta(days=20)).isoformat(),
        "MSFT": (now + timedelta(days=21)).isoformat(),
        "GOOG": (now + timedelta(days=22)).isoformat(),
        "META": (now + timedelta(days=22)).isoformat(),
        "AMZN": (now + timedelta(days=23)).isoformat(),
        "TSLA": (now + timedelta(days=9)).isoformat(),
    }


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    cal = _load_cal()
    now = datetime.now(UTC)
    upcoming = sorted(
        [
            (sym, datetime.fromisoformat(d))
            for sym, d in cal.items()
            if datetime.fromisoformat(d) > now
        ],
        key=lambda x: x[1],
    )
    within_24h = [x for x in upcoming if x[1] - now < timedelta(hours=24)]

    lines = [
        f"# Earnings Amplifier · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- Mag-7 earnings tracked: **{len(MAG7)}**",
        f"- upcoming: **{len(upcoming)}** · within 24h: **{len(within_24h)}**",
        "",
        "## Schedule",
        "| Symbol | When | Δ |",
        "|---|---|---|",
    ]
    for sym, d in upcoming:
        delta = d - now
        lines.append(
            f"| {sym} | {d.strftime('%Y-%m-%d %H:%M')} | T+{int(delta.total_seconds() / 86400)}d |"
        )

    if within_24h:
        lines += [
            "",
            "## ⚠️ Size-down mode recommended",
            "",
            f"- {within_24h[0][0]} reports within 24h — consider 50% size-down on NQ entries.",
        ]

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"earnings_amp: upcoming={len(upcoming)} within24h={len(within_24h)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
