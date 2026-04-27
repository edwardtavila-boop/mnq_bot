"""Phase C #22 — Volume profile (VPOC, VAH, VAL).

Builds a price-binned volume profile from the most recent DataBento
parquet, computes value area (70%), VPOC, VAH, VAL. Output is a
markdown histogram you can paste into the daily prep doc.

Usage:
    python scripts/volume_profile.py
    python scripts/volume_profile.py --bins 40
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "volume_profile.md"

# B2 closure (v0.2.2): canonical bars path resolves via mnq.core.paths.
# Operator override: MNQ_BARS_DATABENTO_DIR.
from mnq.core.paths import BARS_DATABENTO_DIR  # noqa: E402

BARS_DIR = BARS_DATABENTO_DIR


def _read_parquet(path: Path):
    try:
        import pyarrow.parquet as pq  # type: ignore

        return pq.read_table(str(path)).to_pylist()
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bins", type=int, default=30)
    args = p.parse_args()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(BARS_DIR.glob("*.parquet")) if BARS_DIR.exists() else []
    if not files:
        REPORT_PATH.write_text("# Volume Profile\n\n_no bar data_\n")
        print("volume_profile: no bars")
        return 0

    rows = _read_parquet(files[-1])[-500:]
    if not rows:
        REPORT_PATH.write_text("# Volume Profile\n\n_bars unreadable_\n")
        print("volume_profile: bars unreadable")
        return 0

    prices = [(r["high"] + r["low"]) / 2 for r in rows if r.get("high") and r.get("low")]
    vols = [r.get("volume", 0) or 0 for r in rows]
    if not prices:
        REPORT_PATH.write_text("# Volume Profile\n\n_no valid prices_\n")
        return 0

    lo, hi = min(prices), max(prices)
    bin_w = (hi - lo) / args.bins if hi > lo else 1
    buckets: dict = defaultdict(int)
    for p_, v in zip(prices, vols, strict=True):
        idx = min(args.bins - 1, int((p_ - lo) / bin_w)) if bin_w else 0
        buckets[idx] += v
    total = sum(buckets.values()) or 1

    # VPOC
    vpoc_idx = max(buckets, key=buckets.get)
    vpoc_price = lo + (vpoc_idx + 0.5) * bin_w

    # Value area: expand from VPOC outward until 70% captured
    captured = buckets[vpoc_idx]
    lo_idx = hi_idx = vpoc_idx
    while captured / total < 0.7 and (lo_idx > 0 or hi_idx < args.bins - 1):
        up = buckets.get(hi_idx + 1, 0) if hi_idx < args.bins - 1 else -1
        dn = buckets.get(lo_idx - 1, 0) if lo_idx > 0 else -1
        if up >= dn:
            hi_idx += 1
            captured += up
        else:
            lo_idx -= 1
            captured += dn
    vah = lo + (hi_idx + 1) * bin_w
    val = lo + lo_idx * bin_w

    cmax = max(buckets.values())
    lines = [
        f"# Volume Profile · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- source: `{files[-1].name}` · bars: {len(rows)}",
        f"- price range: **{lo:.2f} → {hi:.2f}**",
        f"- VPOC: **{vpoc_price:.2f}**  ·  VAH: **{vah:.2f}**  ·  VAL: **{val:.2f}**",
        f"- value area captures **{captured / total:.0%}** of volume",
        "",
        "## Histogram (top-down)",
        "```",
    ]
    for i in reversed(range(args.bins)):
        px = lo + (i + 0.5) * bin_w
        bar = "█" * int(round(buckets.get(i, 0) / cmax * 40)) if cmax else ""
        marker = "← VPOC" if i == vpoc_idx else " "
        lines.append(f"  {px:8.2f}  {bar:<40}  {marker}")
    lines.append("```")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"volume_profile: VPOC={vpoc_price:.2f} VAL={val:.2f} VAH={vah:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
