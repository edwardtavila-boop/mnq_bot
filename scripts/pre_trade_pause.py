"""Phase B #12 — Pre-trade pause gate.

Writes a small JSON gate at ``data/pre_trade_gate.json`` that the
executor can read before placing the next order. If the gate is HOT
(set by a streak or risk trigger), the executor must refuse entries
until the cool-off expires.

Usage:
    python scripts/pre_trade_pause.py --set --reason "loss streak" --minutes 15
    python scripts/pre_trade_pause.py --clear
    python scripts/pre_trade_pause.py            # show current state
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = REPO_ROOT / "data" / "pre_trade_gate.json"
REPORT_PATH = REPO_ROOT / "reports" / "pre_trade_pause.md"


def _load() -> dict:
    if GATE_PATH.exists():
        return json.loads(GATE_PATH.read_text())
    return {"state": "COLD", "reason": "", "until": None, "since": None}


def _save(gate: dict) -> None:
    GATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GATE_PATH.write_text(json.dumps(gate, indent=2))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--set", action="store_true")
    p.add_argument("--clear", action="store_true")
    p.add_argument("--reason", default="manual")
    p.add_argument("--minutes", type=int, default=15)
    args = p.parse_args()

    now = datetime.now(UTC)
    gate = _load()

    if args.clear:
        gate = {"state": "COLD", "reason": "", "until": None, "since": now.isoformat()}
        _save(gate)
    elif args.set:
        until = (now + timedelta(minutes=args.minutes)).isoformat()
        gate = {"state": "HOT", "reason": args.reason, "until": until, "since": now.isoformat()}
        _save(gate)
    else:
        # Auto-expire
        until = gate.get("until")
        if gate["state"] == "HOT" and until and datetime.fromisoformat(until) <= now:
            gate = {"state": "COLD", "reason": "expired", "until": None, "since": now.isoformat()}
            _save(gate)

    status = "🟢 COLD — entries allowed" if gate["state"] == "COLD" else "🔴 HOT — entries blocked"
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        f"# Pre-trade Pause · {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- status: **{status}**\n- reason: `{gate['reason']}`\n"
        f"- since: `{gate['since']}`\n- until: `{gate['until']}`\n"
    )
    print(f"pre_trade_pause: {gate['state']} · reason={gate['reason']!r} until={gate['until']}")
    return 0 if gate["state"] == "COLD" else 1


if __name__ == "__main__":
    sys.exit(main())
