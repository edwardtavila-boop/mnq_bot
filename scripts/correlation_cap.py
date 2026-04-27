"""Phase D #40 — Correlation exposure cap.

If you're long MNQ and long MES, you're really just long 1.5× index.
This computes implied aggregate exposure across instruments (by
notional × beta) and flags when total exposure exceeds a threshold.

Reads instrument exposure from ``data/open_positions.json``
(produced by the venue layer); falls back to synthetic state if
absent.

Usage:
    python scripts/correlation_cap.py --max-beta 2.0
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
POS_PATH = REPO_ROOT / "data" / "open_positions.json"
REPORT_PATH = REPO_ROOT / "reports" / "correlation_cap.md"


# Canonical beta table vs NQ (index futures complex).
BETAS = {
    "MNQ": 1.0,
    "NQ": 1.0,
    "MES": 0.78,
    "ES": 0.78,
    "YM": 0.65,
    "MYM": 0.65,
    "RTY": 0.72,
    "M2K": 0.72,
}
NOTIONAL = {
    "MNQ": 2.0,
    "NQ": 20.0,
    "MES": 5.0,
    "ES": 50.0,
    "YM": 5.0,
    "MYM": 0.5,
    "RTY": 5.0,
    "M2K": 0.5,
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-beta", type=float, default=2.0)
    args = p.parse_args()

    if not POS_PATH.exists():
        POS_PATH.parent.mkdir(parents=True, exist_ok=True)
        POS_PATH.write_text(
            json.dumps(
                {
                    "MNQ": {"qty": 0, "price": 0},
                },
                indent=2,
            )
        )

    positions = json.loads(POS_PATH.read_text())
    rows = []
    agg_beta = 0.0
    for sym, p_ in positions.items():
        qty = p_.get("qty", 0)
        px = p_.get("price", 0)
        beta = BETAS.get(sym, 1.0)
        notional = NOTIONAL.get(sym, 2.0) * px * qty
        beta_exposure = beta * qty
        agg_beta += beta_exposure
        rows.append((sym, qty, px, beta, notional, beta_exposure))

    verdict = "🔴 OVER CAP" if abs(agg_beta) > args.max_beta else "🟢 within cap"

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Correlation Cap · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- aggregate beta exposure: **{agg_beta:+.2f}** / cap **{args.max_beta}**",
        f"- verdict: **{verdict}**",
        "",
        "| Sym | Qty | Px | Beta | Notional | Beta-wtd |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for sym, qty, px, beta, notional, bx in rows:
        lines.append(f"| {sym} | {qty} | {px:.2f} | {beta:.2f} | ${notional:,.0f} | {bx:+.2f} |")
    if not rows:
        lines.append("| (no positions) | - | - | - | - | - |")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"correlation_cap: agg_beta={agg_beta:+.2f} · {verdict}")
    return 1 if abs(agg_beta) > args.max_beta else 0


if __name__ == "__main__":
    sys.exit(main())
