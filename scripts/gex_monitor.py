"""Phase C #25 — GEX (gamma exposure) monitor — STUB.

Dealer gamma positioning flips from mean-reverting (positive GEX,
dealers long gamma) to trend-amplifying (negative GEX). Emits the
contract and a canned placeholder until SpotGamma / Unusual Whales
feed is wired.

Usage:
    python scripts/gex_monitor.py
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "gex_monitor.md"


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    has_feed = bool(os.environ.get("FIRM_GEX_URL"))
    gex_billion = 0.0  # stub value
    regime = "UNKNOWN" if not has_feed else ("POSITIVE" if gex_billion > 0 else "NEGATIVE")
    regime_meaning = {
        "POSITIVE": "dealers long gamma → mean-reversion, fade extremes",
        "NEGATIVE": "dealers short gamma → trend amplification, follow momentum",
        "UNKNOWN": "no feed configured — no GEX guidance",
    }[regime]

    REPORT_PATH.write_text(
        f"# GEX Monitor · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- dealer gamma exposure: **${gex_billion:.2f}B** (stub)\n"
        f"- regime: **{regime}**\n- interpretation: {regime_meaning}\n\n"
        f"_STUB — wire FIRM_GEX_URL to SpotGamma/UW feed for live values._\n"
    )
    print(f"gex_monitor: {regime} (${gex_billion:.2f}B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
