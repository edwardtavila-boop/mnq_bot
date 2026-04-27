"""Phase C #28 — Econ event calendar.

Loads a local events.yaml (if present) or a canned set of typical
high-impact events for the current week, and flags any within the
next 24h.

Usage:
    python scripts/event_calendar.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "event_calendar.md"
EVENTS_PATH = REPO_ROOT / "data" / "events.json"


def _load_events() -> list[dict]:
    if EVENTS_PATH.exists():
        return json.loads(EVENTS_PATH.read_text())
    # Canned example events — replace with live pull from forexfactory/econoday
    now = datetime.now(UTC)
    return [
        {"ts": (now + timedelta(hours=18)).isoformat(), "name": "CPI (USD)", "impact": "HIGH"},
        {"ts": (now + timedelta(days=2)).isoformat(), "name": "FOMC Minutes", "impact": "HIGH"},
        {"ts": (now + timedelta(days=4)).isoformat(), "name": "NFP", "impact": "HIGH"},
        {"ts": (now + timedelta(hours=32)).isoformat(), "name": "Retail Sales", "impact": "MEDIUM"},
    ]


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    events = _load_events()
    now = datetime.now(UTC)
    upcoming = sorted(
        [
            (datetime.fromisoformat(e["ts"]), e)
            for e in events
            if datetime.fromisoformat(e["ts"]) > now
        ],
        key=lambda x: x[0],
    )
    within_24h = [x for x in upcoming if x[0] - now < timedelta(hours=24)]

    trading_guidance = (
        "🔴 PAUSE — high-impact event within 30min"
        if within_24h
        and (within_24h[0][0] - now) < timedelta(minutes=30)
        and within_24h[0][1]["impact"] == "HIGH"
        else "🟡 CAUTION — high-impact within 24h"
        if within_24h and any(e["impact"] == "HIGH" for _, e in within_24h)
        else "🟢 CLEAR — no high-impact events near"
    )

    lines = [
        f"# Event Calendar · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- upcoming events: **{len(upcoming)}** · within 24h: **{len(within_24h)}**",
        f"- trading guidance: **{trading_guidance}**",
        "",
        "| When | Event | Impact |",
        "|---|---|---|",
    ]
    for ts, e in upcoming[:20]:
        delta = ts - now
        rel = f"T+{int(delta.total_seconds() / 3600)}h"
        lines.append(f"| {ts.strftime('%Y-%m-%d %H:%M')} ({rel}) | {e['name']} | {e['impact']} |")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"event_calendar: {len(upcoming)} upcoming · {len(within_24h)} within 24h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
