"""Phase C #26 — VIX term structure.

Inverted VIX curve (front-month > back-month) = fear regime = NQ
intraday ranges expand. Contango VIX = calm regime = tighter stops
pay off. Stub awaits a live curve feed (CBOE/Polygon).

Usage:
    python scripts/vix_term.py
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "vix_term.md"


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    has_feed = bool(os.environ.get("FIRM_VIX_URL"))
    # Canned flat curve when no feed
    curve = [("VIX",  15.0), ("VIX1M", 15.5), ("VIX3M", 16.0), ("VIX6M", 16.5)]
    inverted = curve[0][1] > curve[1][1]
    verdict = (
        "🔴 INVERTED — fear regime, expect wider ranges"
        if inverted else
        "🟢 CONTANGO — calm regime, tighter stops ok"
    )

    lines = [
        f"# VIX Term Structure · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- feed configured: {'yes' if has_feed else 'no (stub)'}",
        f"- verdict: **{verdict}**",
        "",
        "| Tenor | Level |",
        "|---|---:|",
    ]
    for tenor, level in curve:
        lines.append(f"| {tenor} | {level:.2f} |")
    lines += ["", "_STUB — wire FIRM_VIX_URL for live curve._"]

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"vix_term: {'inverted' if inverted else 'contango'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
