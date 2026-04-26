"""ML scorer that trains a per-signal survival predictor from the live journal.

The goal is to learn ``P(trade is a winner | signal features)`` from the
FILL_REALIZED stream and use that probability at runtime to gate / size
new trades.  Even a modestly-calibrated scorer is valuable: if it can
separate the top-decile of signals from the bottom-decile we can skip
the worst trades (or trade them smaller) without altering the scripted
logic.

What this file contains:

* ``extract_features_from_journal(path)`` — reads FILL_REALIZED events
  out of the SQLite journal and joins them with the matching signal
  context (regime, slippage, exit_reason, time-of-day). Produces a
  polars DataFrame ready for training.
* ``train_scorer(df)`` — fits a logistic-regression classifier on
  ``label = (pnl_dollars > 0)`` using a conservative, interpretable
  feature set. Returns a fitted ``Scorer`` bundle (coefficients +
  feature vocabulary).
* ``Scorer.score(features)`` — at runtime, produces P(win | features).
* ``save`` / ``load`` — pickle the scorer for the strategy to consume.

This is a scaffold: the training set on day 1 will be tiny (n=8 for the
first live_sim run). As the journal accumulates more real trades the
scorer gets sharper.  The strategy side integration is deferred — for
now the scorer is a standalone artifact we write to ``models/scorer.pkl``.
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import polars as pl  # noqa: E402

from mnq.core.paths import LIVE_SIM_JOURNAL  # noqa: E402
from mnq.storage.journal import EventJournal  # noqa: E402
from mnq.storage.schema import FILL_REALIZED  # noqa: E402

DEFAULT_JOURNAL = LIVE_SIM_JOURNAL
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "scorer.pkl"


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


FEATURE_COLS: tuple[str, ...] = (
    "slippage_ticks",
    "entry_slip_ticks",
    "hour_of_day",
    "minute_of_day",
    "is_morning",
    "is_afternoon",
    "side_long",
    "regime_high_vol",
    "regime_trend_up",
    "regime_trend_down",
    "regime_chop",
    "regime_range_bound",
)


def extract_features_from_journal(journal_path: Path | str = DEFAULT_JOURNAL) -> pl.DataFrame:
    """Read FILL_REALIZED events and produce a flat feature/label DataFrame.

    Each row is one closed trade. ``label`` = 1 if ``pnl_dollars > 0``.
    """
    path = Path(journal_path)
    if not path.exists():
        raise FileNotFoundError(f"journal not found: {path}")

    journal = EventJournal(path)

    rows: list[dict[str, Any]] = []
    for entry in journal.replay(event_types=(FILL_REALIZED,)):
        payload = entry.payload
        # Only count trade-closure records: they must carry pnl_dollars.
        if "pnl_dollars" not in payload or "entry_ts" not in payload:
            continue
        try:
            pnl = float(payload["pnl_dollars"])
        except (TypeError, ValueError):
            continue

        entry_ts = payload.get("entry_ts", "")
        # Extract hour/minute from ISO timestamp (HH:MM).
        hh, mm = 0, 0
        if entry_ts:
            try:
                time_part = entry_ts.split("T", 1)[1]
                hh = int(time_part[:2])
                mm = int(time_part[3:5])
            except (IndexError, ValueError):
                hh, mm = 0, 0
        minute_of_day = hh * 60 + mm

        regime = str(payload.get("regime", "unknown"))
        side = str(payload.get("side", "long")).lower()

        row = {
            "pnl_dollars": pnl,
            "label": int(pnl > 0),
            "slippage_ticks": float(payload.get("slippage_ticks", 0.0)),
            "entry_slip_ticks": float(payload.get("entry_slip_ticks", 0.0)),
            "hour_of_day": hh,
            "minute_of_day": minute_of_day,
            "is_morning": int(9 * 60 + 30 <= minute_of_day <= 11 * 60),
            "is_afternoon": int(14 * 60 <= minute_of_day <= 16 * 60),
            "side_long": int(side == "long"),
            "regime_high_vol": int(regime == "high_vol"),
            "regime_trend_up": int(regime == "trend_up"),
            "regime_trend_down": int(regime == "trend_down"),
            "regime_chop": int(regime == "chop"),
            "regime_range_bound": int(regime == "range_bound"),
            "exit_reason": str(payload.get("exit_reason", "")),
        }
        rows.append(row)

    if not rows:
        return pl.DataFrame(schema={"label": pl.Int64, **dict.fromkeys(FEATURE_COLS, pl.Float64)})
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


@dataclass
class Scorer:
    """Logistic-regression scorer with a fixed feature ordering."""

    coefficients: list[float]
    intercept: float
    features: tuple[str, ...] = FEATURE_COLS
    n_train: int = 0
    train_accuracy: float = 0.0
    train_class_balance: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def score(self, feature_dict: dict[str, float]) -> float:
        """Return P(win) for a single signal's feature vector."""
        z = self.intercept
        for name, coef in zip(self.features, self.coefficients, strict=True):
            z += coef * float(feature_dict.get(name, 0.0))
        # Numerically stable sigmoid.
        if z >= 0:
            e = math.exp(-z)
            return 1.0 / (1.0 + e)
        e = math.exp(z)
        return e / (1.0 + e)

    def score_batch(self, df: pl.DataFrame) -> list[float]:
        return [self.score({c: float(r[c]) for c in self.features}) for r in df.iter_rows(named=True)]

    # ----- persistence ----------------------------------------------------

    def save(self, path: Path | str = DEFAULT_MODEL_PATH) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as fh:
            pickle.dump(self, fh)
        return p

    @staticmethod
    def load(path: Path | str = DEFAULT_MODEL_PATH) -> Scorer:
        with Path(path).open("rb") as fh:
            obj = pickle.load(fh)
        if not isinstance(obj, Scorer):
            raise TypeError(f"Expected Scorer, got {type(obj).__name__}")
        return obj


def _sigmoid(z: float) -> float:
    # Numerically stable.
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def _standardize(
    X: list[list[float]],  # noqa: N803  (X is conventional for feature matrix)
) -> tuple[list[list[float]], list[float], list[float]]:
    """Zero-mean / unit-variance each column; return (X', means, stds)."""
    if not X:
        return (X, [], [])
    n = len(X)
    d = len(X[0])
    means = [sum(X[i][j] for i in range(n)) / n for j in range(d)]
    stds: list[float] = []
    for j in range(d):
        v = sum((X[i][j] - means[j]) ** 2 for i in range(n)) / max(1, n - 1)
        stds.append(max(1e-9, math.sqrt(v)))
    xp = [[(X[i][j] - means[j]) / stds[j] for j in range(d)] for i in range(n)]
    return (xp, means, stds)


def _logistic_regression(
    X: list[list[float]],  # noqa: N803  (X is conventional for feature matrix)
    y: list[int],
    *,
    l2: float = 0.1,
    lr: float = 0.05,
    n_iter: int = 300,
) -> tuple[list[float], float]:
    """Tiny gradient-descent logistic regression (no sklearn dependency).

    Good enough for the scaffold; swap for sklearn / lightgbm later.
    Features are standardized internally so mixed scales don't blow up.
    """
    n = len(y)
    if n == 0:
        return ([0.0] * (len(X[0]) if X else 0), 0.0)
    xs, means, stds = _standardize(X)
    d = len(xs[0])
    w = [0.0] * d
    b = 0.0
    for _ in range(n_iter):
        # Gradients
        gw = [0.0] * d
        gb = 0.0
        for i in range(n):
            z = b + sum(w[j] * xs[i][j] for j in range(d))
            p = _sigmoid(z)
            err = p - y[i]
            gb += err
            for j in range(d):
                gw[j] += err * xs[i][j]
        # L2
        for j in range(d):
            gw[j] += l2 * w[j]
        # Update
        b -= lr * gb / n
        for j in range(d):
            w[j] -= lr * gw[j] / n

    # Fold (means, stds) back into the coefficients so the scorer can use
    # raw features at inference time: w_raw_j = w_j / std_j ; b_raw = b - sum(w_j * mean_j / std_j)
    w_raw = [w[j] / stds[j] for j in range(d)]
    b_raw = b - sum(w[j] * means[j] / stds[j] for j in range(d))
    return (w_raw, b_raw)


def train_scorer(df: pl.DataFrame, *, l2: float = 0.1) -> Scorer:
    """Train the logistic-regression scorer from a feature frame."""
    if df.is_empty():
        return Scorer(coefficients=[0.0] * len(FEATURE_COLS), intercept=0.0, n_train=0)

    # Select feature matrix + label.
    x_mat = [[float(row[c]) for c in FEATURE_COLS] for row in df.iter_rows(named=True)]
    y = [int(row["label"]) for row in df.iter_rows(named=True)]

    w, b = _logistic_regression(x_mat, y, l2=l2)

    # Sanity: compute training accuracy (purely diagnostic).
    correct = 0
    for xi, yi in zip(x_mat, y, strict=True):
        z = b + sum(w[j] * xi[j] for j in range(len(w)))
        pred = 1 if z >= 0 else 0
        correct += int(pred == yi)
    acc = correct / len(y)

    return Scorer(
        coefficients=w,
        intercept=b,
        features=FEATURE_COLS,
        n_train=len(y),
        train_accuracy=acc,
        train_class_balance=(sum(y) / len(y)),
        metadata={"l2": l2},
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    ap.add_argument("--out", default=str(DEFAULT_MODEL_PATH))
    ap.add_argument("--summary-json", default=None, help="optional JSON summary path")
    args = ap.parse_args(argv)

    df = extract_features_from_journal(args.journal)
    print(f"Extracted {len(df)} closed trades from {args.journal}")
    if df.is_empty():
        print("No trades — nothing to train on.")
        return 1

    wins = int(df["label"].sum())
    total = len(df)
    print(f"Class balance: {wins}/{total} wins = {wins/total:.1%}")

    scorer = train_scorer(df)
    saved = scorer.save(args.out)
    print(f"Saved scorer to {saved}")
    print(f"Training accuracy: {scorer.train_accuracy:.1%}  (n={scorer.n_train})")

    # Report top positive / negative coefficients for interpretability.
    pairs = sorted(zip(scorer.features, scorer.coefficients, strict=True),
                   key=lambda p: p[1], reverse=True)
    print("\nTop positive (predict WIN):")
    for name, coef in pairs[:5]:
        print(f"  {name:<24s}  {coef:+.4f}")
    print("Top negative (predict LOSS):")
    for name, coef in pairs[-5:]:
        print(f"  {name:<24s}  {coef:+.4f}")

    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(
            json.dumps(
                {
                    "n_train": scorer.n_train,
                    "train_accuracy": scorer.train_accuracy,
                    "train_class_balance": scorer.train_class_balance,
                    "coefficients": dict(
                        zip(scorer.features, scorer.coefficients, strict=True)
                    ),
                    "intercept": scorer.intercept,
                    "metadata": scorer.metadata,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
