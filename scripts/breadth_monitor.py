"""Phase C #27 — Breadth (TRIN + $ADD).

TRIN = (Adv/Dec volume) / (Adv/Dec issues). Below 1.0 = bullish
internals, above 1.0 = bearish. $ADD is NYSE advances minus declines.
Together they confirm whether index rallies have breadth.

Stub form — real feed needs NYSE TICK via broker API.

Usage:
    python scripts/breadth_monitor.py
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "breadth.md"


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    has_feed = bool(os.environ.get("FIRM_BREADTH_URL"))
    trin = 1.00
    add = 0
    confirmation = (
        "🟢 BULLISH confirmation"
        if trin < 0.8 and add > 500
        else "🔴 BEARISH confirmation"
        if trin > 1.2 and add < -500
        else "🟡 NEUTRAL / MIXED"
    )

    REPORT_PATH.write_text(
        f"# Breadth · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- feed: {'live' if has_feed else 'STUB'}\n"
        f"- TRIN: **{trin:.2f}**\n- $ADD: **{add:+d}**\n"
        f"- signal: **{confirmation}**\n\n"
        "_STUB — wire FIRM_BREADTH_URL to broker / polygon breadth snapshot._\n"
    )
    print(f"breadth: TRIN={trin:.2f} ADD={add}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
