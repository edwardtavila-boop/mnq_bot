"""Phase C #23 — Sector rotation read.

Tracks relative strength across the major sector ETFs (XLK, XLF, XLE,
XLY, XLP, XLV, XLI, XLU, XLB, XLRE, XLC) vs SPY. The idea: when tech
is leading, NQ trends cleaner; when defensive sectors lead, expect
chop and size down.

This is a STUB that emits the contract and canned data when real
sector feed isn't wired.

Usage:
    python scripts/sector_rotation.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "sector_rotation.md"

SECTORS = {
    "XLK": ("Technology", "growth"),
    "XLF": ("Financials", "cyclical"),
    "XLE": ("Energy", "cyclical"),
    "XLY": ("Consumer Discretionary", "growth"),
    "XLP": ("Consumer Staples", "defensive"),
    "XLV": ("Healthcare", "defensive"),
    "XLI": ("Industrials", "cyclical"),
    "XLU": ("Utilities", "defensive"),
    "XLB": ("Materials", "cyclical"),
    "XLRE": ("Real Estate", "rate-sensitive"),
    "XLC": ("Communications", "growth"),
}


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # STUB — real implementation would pull 1d %chg vs SPY from data feed.
    # Canned values flagged as neutral so downstream treats it as no-signal.
    rows = [(sym, name, style, 0.0) for sym, (name, style) in SECTORS.items()]
    growth_rs = sum(r[3] for r in rows if r[2] == "growth")
    defensive_rs = sum(r[3] for r in rows if r[2] == "defensive")

    signal = (
        "🟢 TECH-LED (favor NQ longs)" if growth_rs - defensive_rs > 1.0
        else "🔴 DEFENSIVE-LED (chop risk)" if defensive_rs - growth_rs > 1.0
        else "🟡 MIXED / NO SIGNAL"
    )
    lines = [
        f"# Sector Rotation · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- growth RS: **{growth_rs:+.2f}%**",
        f"- defensive RS: **{defensive_rs:+.2f}%**",
        f"- verdict: **{signal}**",
        "",
        "| Sym | Name | Style | RS vs SPY |",
        "|---|---|---|---:|",
    ]
    for sym, name, style, rs in rows:
        lines.append(f"| {sym} | {name} | {style} | {rs:+.2f}% |")

    lines += ["", "_STUB — wire a sector ETF feed (iex/finnhub/polygon) to populate live RS._"]
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"sector_rotation: {signal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
