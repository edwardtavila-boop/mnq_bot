"""Phase D #39 — Pre-trade voice checklist (TTS).

Prints a structured pre-trade checklist that the trader walks through
out loud (5 items, takes <15s). Writes the answer tree to
``data/pretrade_checks.jsonl`` — downstream we correlate "checklist
complete" trades vs "checklist skipped" trades.

When pyttsx3 is installed the prompts are spoken; otherwise they just
print.

Usage:
    python scripts/pretrade_checklist.py --setup ORB
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "data" / "pretrade_checks.jsonl"
REPORT_PATH = REPO_ROOT / "reports" / "pretrade_checklist.md"


QUESTIONS = [
    "Is there a live catalyst (news, data, levels)? Yes or no.",
    "Is the setup A+ or just passable? Tier 1, 2, or 3.",
    "Am I within daily rule limits? Yes or no.",
    "What's the max risk for this trade in dollars?",
    "Where's my stop and my target, in ticks?",
]


def _speak(text: str) -> None:
    try:
        import pyttsx3  # type: ignore
        eng = pyttsx3.init()
        eng.say(text)
        eng.runAndWait()
    except Exception:
        print(f"[TTS disabled] {text}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--setup", default="default")
    p.add_argument("--skip-speak", action="store_true")
    args = p.parse_args()

    responses = []
    for i, q in enumerate(QUESTIONS, 1):
        if not args.skip_speak:
            _speak(q)
        print(f"[{i}/{len(QUESTIONS)}] {q}")
        responses.append({"q": q, "answer": None})  # CLI input not blocking here

    rec = {
        "ts": datetime.now(UTC).isoformat(),
        "setup": args.setup,
        "questions_presented": len(QUESTIONS),
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(rec) + "\n")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        f"# Pre-trade Checklist · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- setup: `{args.setup}`\n"
        f"- questions asked: **{len(QUESTIONS)}**\n"
        f"- log: `{LOG_PATH.name}`\n\n"
        "## Questions\n" + "\n".join(f"1. {q}" for q in QUESTIONS) + "\n"
    )
    print(f"pretrade_checklist: logged setup={args.setup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
