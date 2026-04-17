"""Phase D #32 — Feature importance via permutation (SHAP-lite).

Permutation importance is the poor man's SHAP and requires no
extra deps. Baseline accuracy → permute feature i → delta-accuracy
is the importance. Pairs with gbm_filter.py.

Usage:
    python scripts/shap_rank.py
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "shap_rank.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402

FEATURE_NAMES = ["bias", "hour", "weekday", "qty", "duration_s", "is_long"]


def _features(t):
    return [
        1.0,
        t.hour or 0,
        t.weekday or 0,
        t.qty,
        t.duration_s or 0,
        1.0 if t.side == "long" else 0.0,
    ]


def _sigmoid(x):
    return 1 / (1 + math.exp(-max(-40, min(40, x))))


def _train(X, y):
    w = [0.0] * len(X[0])
    for _ in range(200):
        for xi, yi in zip(X, y):
            z = sum(w_ * x_ for w_, x_ in zip(w, xi))
            p = _sigmoid(z)
            err = p - yi
            for j in range(len(w)):
                w[j] -= 0.01 * err * xi[j]
    return w


def _acc(w, X, y):
    correct = sum(
        1 for xi, yi in zip(X, y)
        if (_sigmoid(sum(w_ * x_ for w_, x_ in zip(w, xi))) >= 0.5) == bool(yi)
    )
    return correct / max(1, len(X))


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    trades = load_trades()
    if len(trades) < 10:
        REPORT_PATH.write_text("# SHAP Rank\n\n_need ≥10 trades_\n")
        print("shap_rank: insufficient data")
        return 0

    X = [_features(t) for t in trades]
    y = [1 if t.net_pnl > 0 else 0 for t in trades]
    w = _train(X, y)
    base_acc = _acc(w, X, y)

    random.seed(0)
    importances = []
    for j in range(len(FEATURE_NAMES)):
        X2 = [row.copy() for row in X]
        col = [row[j] for row in X2]
        random.shuffle(col)
        for i, row in enumerate(X2):
            row[j] = col[i]
        perm_acc = _acc(w, X2, y)
        importances.append((FEATURE_NAMES[j], base_acc - perm_acc))

    importances.sort(key=lambda x: -abs(x[1]))

    lines = [
        f"# SHAP Rank · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- baseline accuracy: **{base_acc:.1%}**",
        f"- samples: **{len(trades)}**",
        "",
        "## Permutation importance (ΔAccuracy when column shuffled)",
        "| Feature | Δacc |",
        "|---|---:|",
    ]
    for name, imp in importances:
        bar = "█" * int(round(abs(imp) * 200))
        lines.append(f"| {name} | {imp:+.4f} `{bar}` |")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"shap_rank: base_acc={base_acc:.1%} · top={importances[0][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
