"""Phase D #33 — Trade clustering (k-means, stdlib only).

Groups trades into 3 clusters by (hour, duration, pnl). The shape of
each cluster reveals the "trade archetypes" in the journal — you can
then ask which archetype is profitable.

Usage:
    python scripts/trade_clusters.py --k 3
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "trade_clusters.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def _kmeans(points, k, iters=50, seed=0):
    random.seed(seed)
    centers = [list(p) for p in random.sample(points, k)]
    for _ in range(iters):
        groups = [[] for _ in range(k)]
        for p in points:
            dists = [sum((a - b) ** 2 for a, b in zip(p, c, strict=False)) for c in centers]
            groups[dists.index(min(dists))].append(p)
        new_centers = []
        for g, c in zip(groups, centers, strict=False):
            if g:
                new_centers.append([statistics.fmean([p[i] for p in g]) for i in range(len(c))])
            else:
                new_centers.append(c)
        if all(
            math.isclose(new_centers[i][j], centers[i][j]) for i in range(k) for j in range(len(c))
        ):
            break
        centers = new_centers
    groups = [[] for _ in range(k)]
    for p in points:
        dists = [sum((a - b) ** 2 for a, b in zip(p, c, strict=False)) for c in centers]
        groups[dists.index(min(dists))].append(p)
    return centers, groups


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--k", type=int, default=3)
    args = p.parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    trades = load_trades()
    if len(trades) < args.k:
        REPORT_PATH.write_text(f"# Trade Clusters\n\n_need ≥{args.k} trades_\n")
        print("trade_clusters: insufficient data")
        return 0

    points = [(t.hour or 0, t.duration_s or 0, t.net_pnl) for t in trades]
    centers, groups = _kmeans(points, args.k)

    lines = [
        f"# Trade Clusters · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades: **{len(trades)}** · clusters: **{args.k}**",
        "",
        "## Cluster summary",
        "| # | N | Center (hour / dur_s / pnl) | Mean PnL | Hit rate |",
        "|---|---:|---|---:|---:|",
    ]
    for i, (c, g) in enumerate(zip(centers, groups, strict=False)):
        if not g:
            continue
        mean_pnl = statistics.fmean([p[2] for p in g])
        wr = sum(1 for p in g if p[2] > 0) / len(g)
        lines.append(
            f"| C{i} | {len(g)} | H{c[0]:.1f} / {c[1]:.0f}s / ${c[2]:+.2f} | "
            f"${mean_pnl:+.2f} | {wr:.0%} |"
        )

    lines += [
        "",
        "## Interpretation",
        "- The cluster with highest hit-rate × mean PnL is your A+ archetype.",
        "- Clusters with negative mean PnL are skip candidates.",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"trade_clusters: k={args.k} over {len(trades)} trades")
    return 0


if __name__ == "__main__":
    sys.exit(main())
