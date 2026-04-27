"""Calibration scorer — Brier score / log-loss / reliability on the ml_scorer.

Phase 3 of the roadmap. A scorer that predicts P(win) is only useful if
its probabilities are calibrated — i.e., when it says 0.7 the realized win
rate on those signals is close to 0.7. This script computes:

* Brier score (lower is better; 0.25 = coin-flip baseline when p=0.5).
* Log-loss (cross-entropy; lower is better).
* Reliability-curve bins (predicted-bucket → realized win rate).
* LOOCV versions of both metrics, which for small-n journals (the current
  live_sim run has n=8) are the only honest measure of generalization.

Usage:

    python scripts/calibration.py \\
        --journal /sessions/kind-keen-faraday/data/live_sim/journal.sqlite \\
        --model   models/scorer.pkl \\
        --output  reports/calibration.md

When ``--model`` is omitted, the script trains a fresh logistic scorer
inline (on the same journal) and reports in-sample + LOOCV metrics.

The script is self-contained — no desktop_app/firm imports.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ml_scorer import (  # noqa: E402
    FEATURE_COLS,
    Scorer,
    extract_features_from_journal,
    train_scorer,
)

from mnq.core.paths import LIVE_SIM_JOURNAL  # noqa: E402

DEFAULT_JOURNAL = LIVE_SIM_JOURNAL
DEFAULT_MODEL = REPO_ROOT / "models" / "scorer.pkl"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "calibration.md"


@dataclass(frozen=True)
class CalibrationReport:
    """Aggregate of all calibration metrics for one scorer / dataset pair."""

    n: int
    brier: float
    log_loss: float
    brier_loocv: float | None
    log_loss_loocv: float | None
    reliability: list[tuple[float, float, int]]  # (mean_pred, mean_obs, n_in_bin)
    base_rate: float

    def render_markdown(self, title: str = "Calibration Report") -> str:
        """Render the report as a markdown string."""
        lines: list[str] = [f"# {title}", ""]
        lines.append(f"- n = **{self.n}**")
        lines.append(f"- base rate (empirical win rate) = **{self.base_rate:.3f}**")
        lines.append(f"- Brier score (in-sample) = **{self.brier:.4f}**")
        lines.append(f"- Log-loss (in-sample) = **{self.log_loss:.4f}**")
        if self.brier_loocv is not None:
            lines.append(f"- Brier score (LOOCV) = **{self.brier_loocv:.4f}**")
        if self.log_loss_loocv is not None:
            lines.append(f"- Log-loss (LOOCV) = **{self.log_loss_loocv:.4f}**")
        lines.append("")
        lines.append("## Reliability curve")
        lines.append("")
        lines.append("| bucket pred-mean | realized win rate | n |")
        lines.append("|---:|---:|---:|")
        for mean_pred, mean_obs, k in self.reliability:
            lines.append(f"| {mean_pred:.3f} | {mean_obs:.3f} | {k} |")
        lines.append("")
        lines.append("## Interpretation")
        lines.append("")
        lines.append(
            "* Brier ≥ ``p*(1-p)`` (with p = base rate) means the scorer adds no "
            "information beyond the base rate — it is no better than always "
            "predicting the empirical win rate."
        )
        lines.append(
            "* A reliability curve that hugs the y=x line indicates good calibration. "
            "A curve that systematically over-promises (pred > realized) means the "
            "scorer is over-confident; under-promises means it is under-confident."
        )
        lines.append(
            "* LOOCV > in-sample by a wide margin is the classic overfit signature. "
            "When n is small (journals fresh off a 15-day run), treat LOOCV as the "
            "primary number and in-sample as a sanity check."
        )
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------


def brier_score(probs: list[float], labels: list[int]) -> float:
    """Mean squared error between probabilities and 0/1 labels."""
    if not probs:
        return float("nan")
    s = 0.0
    for p, y in zip(probs, labels, strict=True):
        s += (p - y) ** 2
    return s / len(probs)


def log_loss(probs: list[float], labels: list[int], *, eps: float = 1e-12) -> float:
    """Binary cross-entropy, clipping probs into (eps, 1-eps) for stability."""
    if not probs:
        return float("nan")
    s = 0.0
    for p, y in zip(probs, labels, strict=True):
        pp = min(1.0 - eps, max(eps, p))
        s += -y * math.log(pp) - (1 - y) * math.log(1.0 - pp)
    return s / len(probs)


def reliability_bins(
    probs: list[float], labels: list[int], *, n_bins: int = 5
) -> list[tuple[float, float, int]]:
    """Bucket by predicted probability; report (mean pred, mean obs, count)."""
    if not probs:
        return []
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, labels, strict=True):
        idx = min(n_bins - 1, int(p * n_bins))
        buckets[idx].append((p, y))
    out: list[tuple[float, float, int]] = []
    for b in buckets:
        if not b:
            continue
        mean_p = sum(p for p, _ in b) / len(b)
        mean_y = sum(y for _, y in b) / len(b)
        out.append((mean_p, mean_y, len(b)))
    return out


# ---------------------------------------------------------------------------
# LOOCV helpers
# ---------------------------------------------------------------------------


def _loocv_probs(
    feature_matrix: list[list[float]], labels: list[int], *, l2: float = 0.1
) -> list[float]:
    """Leave-one-out refit; return held-out probabilities in the original order."""
    # Avoid a heavy dependency; call back into train_scorer's minimal logistic.
    from ml_scorer import _logistic_regression, _sigmoid

    held_out_probs: list[float] = []
    n = len(labels)
    for i in range(n):
        x_train = [feature_matrix[j] for j in range(n) if j != i]
        y_train = [labels[j] for j in range(n) if j != i]
        if not y_train or all(y == y_train[0] for y in y_train):
            # Degenerate fold (only one class in training) — fall back to the
            # training-set class balance as the prediction.
            held_out_probs.append(sum(y_train) / max(1, len(y_train)))
            continue
        w, b = _logistic_regression(x_train, y_train, l2=l2)
        xi = feature_matrix[i]
        z = b + sum(w[j] * xi[j] for j in range(len(w)))
        held_out_probs.append(_sigmoid(z))
    return held_out_probs


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def compute_report(
    journal_path: Path | str = DEFAULT_JOURNAL,
    model_path: Path | str | None = None,
    *,
    n_bins: int = 5,
    l2: float = 0.1,
) -> CalibrationReport:
    """Load journal + scorer, compute the full calibration report."""
    df = extract_features_from_journal(journal_path)
    if df.is_empty():
        return CalibrationReport(0, float("nan"), float("nan"), None, None, [], float("nan"))

    features = [[float(row[c]) for c in FEATURE_COLS] for row in df.iter_rows(named=True)]
    labels = [int(row["label"]) for row in df.iter_rows(named=True)]
    base_rate = sum(labels) / len(labels)

    # Scorer: either loaded from disk or retrained fresh.
    if model_path is not None and Path(model_path).exists():
        scorer = Scorer.load(model_path)
    else:
        scorer = train_scorer(df, l2=l2)

    # In-sample predictions.
    probs_in = [
        scorer.score({c: float(row[c]) for c in FEATURE_COLS}) for row in df.iter_rows(named=True)
    ]

    brier_in = brier_score(probs_in, labels)
    ll_in = log_loss(probs_in, labels)
    rel = reliability_bins(probs_in, labels, n_bins=n_bins)

    # LOOCV — only meaningful with n >= 4 AND both classes present.
    brier_loo: float | None = None
    ll_loo: float | None = None
    if len(labels) >= 4 and 0 < sum(labels) < len(labels):
        probs_loo = _loocv_probs(features, labels, l2=l2)
        brier_loo = brier_score(probs_loo, labels)
        ll_loo = log_loss(probs_loo, labels)

    return CalibrationReport(
        n=len(labels),
        brier=brier_in,
        log_loss=ll_in,
        brier_loocv=brier_loo,
        log_loss_loocv=ll_loo,
        reliability=rel,
        base_rate=base_rate,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibration metrics for ml_scorer.")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--n-bins", type=int, default=5)
    args = parser.parse_args(argv)

    report = compute_report(args.journal, args.model, n_bins=args.n_bins)
    md = report.render_markdown(title="ml_scorer — Calibration Report")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
