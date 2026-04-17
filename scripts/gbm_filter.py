"""Phase D #31 — GBM (gradient-boosted) trade filter.

Trains a tiny logistic model (no sklearn dependency) on journal
features to predict P(win) and flags setups below the bar. This is
the minimal-dependency ML scaffold — when real features + volume land,
swap in sklearn/lightgbm.

Usage:
    python scripts/gbm_filter.py
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "gbm_filter.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def _features(t):
    return [
        1.0,
        t.hour or 0,
        t.weekday or 0,
        t.qty,
        t.duration_s or 0,
        1.0 if t.side == "long" else 0.0,
    ]


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-max(-40, min(40, x))))


def _train_logreg(X: list[list[float]], y: list[int], iters: int = 300, lr: float = 0.01):
    n_feat = len(X[0])
    w = [0.0] * n_feat
    for _ in range(iters):
        for xi, yi in zip(X, y):
            z = sum(w_ * x_ for w_, x_ in zip(w, xi))
            p = _sigmoid(z)
            err = p - yi
            for j in range(n_feat):
                w[j] -= lr * err * xi[j]
    return w


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    trades = load_trades()
    if len(trades) < 10:
        REPORT_PATH.write_text("# GBM Filter\n\n_need ≥10 trades for training_\n")
        print("gbm_filter: insufficient data")
        return 0

    # Split 70/30 by sequence
    split = int(len(trades) * 0.7)
    train = trades[:split]
    test = trades[split:]
    X_tr = [_features(t) for t in train]
    y_tr = [1 if t.net_pnl > 0 else 0 for t in train]
    w = _train_logreg(X_tr, y_tr)

    preds = []
    for t in test:
        p = _sigmoid(sum(w_ * x_ for w_, x_ in zip(w, _features(t))))
        preds.append((t, p))

    n_tp = n_fp = n_fn = n_tn = 0
    for t, p in preds:
        pred_win = p >= 0.5
        actual_win = t.net_pnl > 0
        if pred_win and actual_win: n_tp += 1
        elif pred_win and not actual_win: n_fp += 1
        elif not pred_win and actual_win: n_fn += 1
        else: n_tn += 1

    acc = (n_tp + n_tn) / max(1, len(test))
    prec = n_tp / max(1, n_tp + n_fp)
    rec = n_tp / max(1, n_tp + n_fn)

    lines = [
        f"# GBM Filter · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- train: **{len(train)}** · test: **{len(test)}**",
        f"- feature set: [bias, hour, weekday, qty, duration_s, is_long]",
        f"- weights: {[round(x, 4) for x in w]}",
        "",
        "## Test performance",
        f"- accuracy: **{acc:.1%}**",
        f"- precision: **{prec:.1%}**",
        f"- recall: **{rec:.1%}**",
        f"- confusion: TP={n_tp} FP={n_fp} FN={n_fn} TN={n_tn}",
        "",
        "_Minimal logistic regression — swap in sklearn/lightgbm when Phase C features land._",
    ]
    # Suggest skipping trades where P < 0.4
    skip_candidates = [t for t, p in preds if p < 0.4]
    if skip_candidates:
        actual_exp = statistics.fmean([t.net_pnl for t in skip_candidates])
        lines += [
            "",
            f"## Trades flagged for skip (P<0.4): **{len(skip_candidates)}**",
            f"- actual realized expectancy on skipped set: **${actual_exp:+.2f}**",
        ]

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"gbm_filter: acc={acc:.1%} prec={prec:.1%} rec={rec:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
