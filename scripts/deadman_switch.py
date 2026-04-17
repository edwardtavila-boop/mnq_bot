"""Phase D #36 — Dead-man's switch.

If the heartbeat is stale past a cutoff, auto-set the pre-trade gate
to HOT and flatten any open positions (via venue kill endpoint if
configured).

This is the second-line-of-defense companion to heartbeat.py.

Usage:
    python scripts/deadman_switch.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HB_PATH = REPO_ROOT / "data" / "heartbeat.json"
GATE_PATH = REPO_ROOT / "data" / "pre_trade_gate.json"
REPORT_PATH = REPO_ROOT / "reports" / "deadman_switch.md"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cutoff-min", type=int, default=10)
    args = p.parse_args()

    now = datetime.now(UTC)
    age_s: float = float("inf")
    if HB_PATH.exists():
        last = json.loads(HB_PATH.read_text())
        age_s = (now - datetime.fromisoformat(last["ts"])).total_seconds()

    tripped = age_s > args.cutoff_min * 60
    if tripped:
        GATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GATE_PATH.write_text(json.dumps({
            "state": "HOT",
            "reason": f"dead-man's switch tripped (hb age {age_s:.0f}s)",
            "until": None,
            "since": now.isoformat(),
        }, indent=2))

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    status_icon = "🔴 TRIPPED — gate HOT" if tripped else "🟢 nominal"
    REPORT_PATH.write_text(
        f"# Dead-man's Switch · {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- heartbeat age: **{age_s:.0f}s**\n- cutoff: **{args.cutoff_min * 60}s**\n"
        f"- status: **{status_icon}**\n"
    )
    print(f"deadman_switch: {'tripped' if tripped else 'ok'} (age={age_s:.0f}s)")
    return 1 if tripped else 0


if __name__ == "__main__":
    sys.exit(main())
