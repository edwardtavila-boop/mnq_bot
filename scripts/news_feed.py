"""Phase C #24 — News feed ingestor.

Polls an RSS or NewsAPI endpoint (configured via FIRM_NEWS_URL env
var) and scores headlines for market impact. Falls back to a
canned no-op when no URL is configured.

Usage:
    python scripts/news_feed.py
    FIRM_NEWS_URL=https://example.com/rss python scripts/news_feed.py
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "news_feed.md"

HIGH_IMPACT = (
    "FOMC",
    "CPI",
    "PPI",
    "NFP",
    "GDP",
    "Powell",
    "rate cut",
    "rate hike",
    "inflation",
    "jobless",
    "retail sales",
    "PCE",
    "earnings",
)


def _score(title: str) -> int:
    t = title.upper()
    return sum(1 for kw in HIGH_IMPACT if kw.upper() in t)


def _fetch(url: str) -> list[tuple[str, str]]:
    try:
        with urlrequest.urlopen(url, timeout=10) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []
    try:
        root = ET.fromstring(body)
        out = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            out.append((title, link))
        return out
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    url = os.environ.get("FIRM_NEWS_URL", "")
    items = _fetch(url) if url else []
    scored = sorted(((t, l, _score(t)) for t, l in items), key=lambda x: -x[2])

    high = [x for x in scored if x[2] >= 2]
    med = [x for x in scored if x[2] == 1]

    lines = [
        f"# News Feed · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- source: `{url or '(not configured — set FIRM_NEWS_URL)'}`",
        f"- items: **{len(items)}**",
        f"- high-impact (≥2 keywords): **{len(high)}**",
        f"- med-impact (1 keyword): **{len(med)}**",
        "",
    ]
    if high:
        lines += ["## High impact", ""]
        for t, l, s in high[:10]:
            lines.append(f"- [{t}]({l}) — score {s}")
    if med:
        lines += ["", "## Medium impact", ""]
        for t, l, s in med[:10]:
            lines.append(f"- [{t}]({l}) — score {s}")
    if not items:
        lines.append("_No feed configured / no items fetched — running as stub._")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"news_feed: items={len(items)} · high={len(high)} med={len(med)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
