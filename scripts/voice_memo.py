"""Phase B #16 — Voice memo capture stub.

Logs a spoken reflection for a given trade. Accepts transcript text
(from external STT — MacWhisper, Whisper API, etc.) and stores it in
``data/voice_memos.jsonl`` keyed by trade seq.

Usage:
    python scripts/voice_memo.py --seq 42 --text "Chased the breakout, ignored the divergence"
    python scripts/voice_memo.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMO_PATH = REPO_ROOT / "data" / "voice_memos.jsonl"
REPORT_PATH = REPO_ROOT / "reports" / "voice_memos.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def _append(rec: dict) -> None:
    MEMO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MEMO_PATH.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def _load() -> list[dict]:
    if not MEMO_PATH.exists():
        return []
    return [json.loads(ln) for ln in MEMO_PATH.read_text().splitlines() if ln.strip()]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seq", type=int)
    p.add_argument("--text", default="")
    p.add_argument("--audio", default="", help="Path to .wav/.m4a (not transcribed here)")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    if args.list or not args.text:
        memos = _load()
        trades = {t.seq: t for t in load_trades()}
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Voice Memos · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            f"- memos logged: **{len(memos)}**",
            "",
            "| Seq | When | Trade | Memo |",
            "|---:|---|---|---|",
        ]
        for m in memos:
            t = trades.get(m.get("seq"))
            ctx = f"${t.net_pnl:+.2f}" if t else "?"
            lines.append(f"| {m.get('seq')} | {m['ts'][:19]} | {ctx} | {m.get('text','')[:80]} |")
        REPORT_PATH.write_text("\n".join(lines) + "\n")
        print(f"voice_memo: {len(memos)} memos")
        return 0

    rec = {
        "seq": args.seq,
        "text": args.text,
        "audio": args.audio,
        "ts": datetime.now(UTC).isoformat(),
    }
    _append(rec)
    print(f"voice_memo: logged seq={args.seq}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
