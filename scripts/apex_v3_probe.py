#!/usr/bin/env python3
"""Apex V3 engine probe — fast availability check.

Lightweight companion to ``eta_v3_bridge.py``. Only asks:

  1. Is ``eta_v3_framework.python.firm_engine`` importable?
  2. How many ``voice_*`` callables does it expose?
  3. Are ``evaluate`` and ``detect_regime`` present?

Writes ``reports/eta_v3_probe.md`` and prints a one-line console
summary. Always exits 0 — observation only, never enforcement.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.eta_v3 import probe_eta_v3_engine  # noqa: E402

REPORT = REPO_ROOT / "reports" / "eta_v3_probe.md"


def main() -> int:
    result = probe_eta_v3_engine()
    now = datetime.now(UTC).isoformat()

    lines = [
        f"# Apex V3 Probe — {now}",
        "",
        f"**Engine available:** {'🟢 yes' if result.get('available') else '🔴 no'}",
        "",
    ]
    if result.get("available"):
        lines.extend(
            [
                f"- Voices found: **{result.get('voices_found', 0)}**",
                f"- `evaluate` callable: **{result.get('has_evaluate')}**",
                f"- `detect_regime` callable: **{result.get('has_detect_regime')}**",
                "",
                "## Voice names",
                "",
                "```",
                "\n".join(result.get("voice_names", []) or ["<none>"]),
                "```",
            ]
        )
    else:
        lines.extend(
            [
                f"- Reason: `{result.get('reason', 'unknown')}`",
                "",
                "The adapter's contract is fail-open: with the engine unavailable,",
                "``apex_to_firm_payload(base, None)`` returns the base payload",
                "unchanged. No trading path is affected.",
            ]
        )

    lines.extend(
        [
            "",
            "## Raw probe",
            "",
            "```json",
            json.dumps(result, indent=2, sort_keys=True, default=str),
            "```",
        ]
    )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")

    status = "🟢 LIVE" if result.get("available") else "🟡 FAIL-OPEN"
    vcount = result.get("voices_found", 0)
    print(f"eta_v3_probe: {status}  ·  voices={vcount}  ·  report={REPORT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
