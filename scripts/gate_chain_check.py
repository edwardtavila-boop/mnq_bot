#!/usr/bin/env python3
"""Pre-trade gate chain health check.

Runs the default gate chain and writes ``reports/gate_chain.md``.
Exits 0 always — this is a status report, not an enforcer. The
executor itself vetoes orders via the chain. Scripts remain
reporters so orchestrator stays green on HOT states.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mnq.risk import build_default_chain  # noqa: E402

REPORT = REPO_ROOT / "reports" / "gate_chain.md"


def main() -> int:
    chain = build_default_chain()
    snap = chain.summary()
    gates = snap["gates"]
    allow_all = snap["allow_all"]

    lines: list[str] = [
        f"# Gate chain — {snap['generated']}",
        "",
        f"**Chain verdict:** {'🟢 ALLOW' if allow_all else '🔴 DENY'}  ·  {len(gates)} gates",
        "",
        "| Gate | Verdict | Reason | Context |",
        "|---|---|---|---|",
    ]
    for g in gates:
        verdict = "🟢 ALLOW" if g["allow"] else "🔴 DENY"
        ctx_str = ", ".join(f"{k}={v}" for k, v in (g.get("context") or {}).items()) or "—"
        lines.append(f"| `{g['name']}` | {verdict} | {g['reason']} | {ctx_str} |")
    lines.append("")
    lines.append("This report is read-only. Enforcement happens in")
    lines.append("`src/mnq/executor/orders.py` via the chain itself.")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines))
    print(f"gate_chain: {'🟢 ALLOW' if allow_all else '🔴 DENY'} · {len(gates)} gates")
    for g in gates:
        mark = "🟢" if g["allow"] else "🔴"
        print(f"  {mark} {g['name']}: {g['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
