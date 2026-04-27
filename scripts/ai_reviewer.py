"""Phase B #20 — AI post-trade reviewer.

For the last N closed trades, generates a deterministic
"what-would-an-experienced-reviewer-say" critique using pattern
matching over journal fields — no LLM dependency required. When the
Anthropic SDK is importable, it can optionally call Claude for a
richer take.

Usage:
    python scripts/ai_reviewer.py --last 10
    python scripts/ai_reviewer.py --last 5 --llm   # uses ANTHROPIC_API_KEY if set
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "ai_reviewer.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def _heuristic_review(t) -> list[str]:
    notes = []
    if t.r_multiple < -1.5:
        notes.append("Loss exceeded 1.5R — stop was honored? If not, tag `ignored stop`.")
    if t.r_multiple > 3:
        notes.append("Winner ≥3R — did the scale-out leave meat on the table? Review MFE.")
    if t.duration_s and t.duration_s < 30:
        notes.append("Held <30s — was this a scalp by design, or a flinch exit?")
    if t.duration_s and t.duration_s > 1800:
        notes.append("Held >30min on an intraday setup — was the thesis still valid at exit?")
    if t.qty > 4:
        notes.append(f"Position size {t.qty} — above normal. Confirm conviction / context.")
    if t.hour is not None and t.hour < 13:
        notes.append(
            f"Opened pre-13:00 UTC (H{t.hour:02d}) — watch for thin-liquidity false moves."
        )
    if not notes:
        notes.append("No flags. Clean trade within policy.")
    return notes


def _llm_review(t) -> str:
    """Optional LLM-powered review. Requires anthropic SDK + ANTHROPIC_API_KEY."""
    try:
        import anthropic  # type: ignore
    except ImportError:
        return "[LLM disabled — anthropic package not installed]"
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return "[LLM disabled — ANTHROPIC_API_KEY not set]"
    try:
        c = anthropic.Anthropic(api_key=key)
        prompt = (
            f"Reviewing a futures trade: {t.side} {t.qty}× MNQ, entered "
            f"${t.entry_price:.2f} exited ${t.exit_price:.2f}, held {t.duration_s:.0f}s, "
            f"net ${t.net_pnl:+.2f} ({t.r_multiple:+.2f}R). Give one sentence of critique."
        )
        resp = c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text if resp.content else "[empty response]"
    except Exception as exc:  # noqa: BLE001
        return f"[LLM error: {exc}]"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--last", type=int, default=10)
    p.add_argument("--llm", action="store_true")
    args = p.parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# AI Reviewer\n\n_no trades in journal_\n")
        print("ai_reviewer: no trades")
        return 0

    targets = trades[-args.last :]
    lines = [
        f"# AI Reviewer · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"Reviewing last **{len(targets)}** trades — "
        f"{'LLM-augmented' if args.llm else 'heuristic only'}.",
        "",
    ]
    for t in targets:
        header = (
            f"### Trade #{t.seq} · {t.side} {t.qty}c · ${t.net_pnl:+.2f} ({t.r_multiple:+.2f}R)"
        )
        lines.append(header)
        for n in _heuristic_review(t):
            lines.append(f"- {n}")
        if args.llm:
            lines.append(f"> {_llm_review(t)}")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"ai_reviewer: reviewed {len(targets)} trades")
    return 0


if __name__ == "__main__":
    sys.exit(main())
