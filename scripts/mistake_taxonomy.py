"""Phase B #19 — Mistake taxonomy aggregator.

Loads all tagged mistakes from ``data/mistakes.jsonl`` (appended by
rule_adherence + voice_memo + manual tagging) and produces a ranked
list of recurring mistake categories, their frequency, and dollar
damage attributed.

Usage:
    python scripts/mistake_taxonomy.py
    python scripts/mistake_taxonomy.py --tag seq=42 --category "chased breakout"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MISTAKE_PATH = REPO_ROOT / "data" / "mistakes.jsonl"
REPORT_PATH = REPO_ROOT / "reports" / "mistake_taxonomy.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402

CANONICAL_CATEGORIES = [
    "chased breakout",
    "faded strength",
    "revenge trade",
    "oversize",
    "off-hours",
    "ignored stop",
    "moved stop",
    "held too long",
    "cut winner early",
    "skipped pre-trade check",
    "traded news event",
    "FOMO entry",
    "doubled down on loser",
    "no-edge setup",
]


def _load_mistakes() -> list[dict]:
    if not MISTAKE_PATH.exists():
        return []
    return [json.loads(ln) for ln in MISTAKE_PATH.read_text().splitlines() if ln.strip()]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", help="seq=<n>")
    p.add_argument("--category", help="mistake category")
    p.add_argument("--note", default="")
    args = p.parse_args()

    if args.tag and args.category:
        seq = int(args.tag.split("=")[1])
        rec = {
            "seq": seq,
            "category": args.category,
            "note": args.note,
            "ts": datetime.now(UTC).isoformat(),
        }
        MISTAKE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MISTAKE_PATH.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"mistake_taxonomy: logged {rec}")

    mistakes = _load_mistakes()
    trades = {t.seq: t for t in load_trades()}

    freq: Counter[str] = Counter()
    damage: dict = defaultdict(float)
    for m in mistakes:
        cat = m["category"]
        freq[cat] += 1
        t = trades.get(m["seq"])
        if t and t.net_pnl < 0:
            damage[cat] += t.net_pnl

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Mistake Taxonomy · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- tagged mistakes: **{len(mistakes)}**",
        f"- unique categories: **{len(freq)}**",
        "",
        "## Canonical vocabulary",
    ]
    lines.extend(f"- `{c}`" for c in CANONICAL_CATEGORIES)

    lines += ["", "## Frequency × damage", "| Category | Count | $ Damage |", "|---|---:|---:|"]
    for cat, count in freq.most_common():
        lines.append(f"| {cat} | {count} | ${damage.get(cat, 0):+.2f} |")
    if not freq:
        lines.append("| (no mistakes tagged yet) | - | - |")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"mistake_taxonomy: {len(mistakes)} tagged · {len(freq)} categories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
