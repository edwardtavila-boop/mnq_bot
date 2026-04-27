"""Phase D #35 — Process heartbeat.

Writes a heartbeat file every run; external monitor checks the
mtime and pages oncall when it goes stale. Simple, reliable, works
without any infra beyond the filesystem.

Usage:
    python scripts/heartbeat.py --beat
    python scripts/heartbeat.py --check --threshold-min 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HB_PATH = REPO_ROOT / "data" / "heartbeat.json"
REPORT_PATH = REPO_ROOT / "reports" / "heartbeat.md"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--beat", action="store_true", help="Write a fresh heartbeat")
    p.add_argument("--check", action="store_true", help="Check for staleness")
    p.add_argument("--threshold-min", type=int, default=5)
    args = p.parse_args()

    now = datetime.now(UTC)
    if args.beat or (not args.check and not args.beat):
        HB_PATH.parent.mkdir(parents=True, exist_ok=True)
        HB_PATH.write_text(json.dumps({"ts": now.isoformat(), "host": "firm-node"}, indent=2))

    status = "UNKNOWN"
    age_s: float = 0
    if HB_PATH.exists():
        last = json.loads(HB_PATH.read_text())
        last_ts = datetime.fromisoformat(last["ts"])
        age_s = (now - last_ts).total_seconds()
        status = "🟢 ALIVE" if age_s < args.threshold_min * 60 else "🔴 STALE"
    else:
        status = "🔴 NO HEARTBEAT"

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        f"# Heartbeat · {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- status: **{status}**\n- last beat age: **{age_s:.1f}s**\n"
        f"- threshold: {args.threshold_min * 60}s\n"
    )
    print(f"heartbeat: {status} (age={age_s:.1f}s)")
    return 0 if status.startswith("🟢") else 1


if __name__ == "__main__":
    sys.exit(main())
