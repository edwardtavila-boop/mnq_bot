"""Per-epoch rolling calibration for gauntlet scores.

Batch 7C. Splits a multi-day trade stream into rolling epochs and
computes calibration metrics (Brier score, log-loss) per epoch. Detects
drift when an epoch's metric deviates beyond a z-score threshold from
the running mean.

The gauntlet's ``score`` field (0.0–1.0) on each gate verdict serves as
the predicted probability that the gate's condition is favourable. A
well-calibrated gauntlet produces scores where, e.g., a 0.8 truly
corresponds to an 80% realized win rate. Drift indicates the gates'
numeric scores are mis-calibrated relative to recent outcomes.

Usage:

    from mnq.gauntlet.rolling_calibration import (
        EpochMetrics,
        RollingCalibration,
        rolling_calibration_report,
    )

    # outcomes: list of (gauntlet_score, label) per trade
    cal = RollingCalibration(window=60, step=30)
    epochs = cal.evaluate(outcomes)
    drift_epochs = [e for e in epochs if e.drift_alert]
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EpochMetrics:
    """Calibration metrics for a single rolling epoch."""

    epoch_idx: int
    start: int  # index into outcomes list
    end: int  # exclusive
    n: int
    brier: float
    log_loss: float
    base_rate: float
    mean_pred: float
    z_brier: float | None  # z-score vs running mean (None for first epoch)
    z_log_loss: float | None
    drift_alert: bool  # True if |z| > threshold for either metric


def _brier(preds: list[float], labels: list[int]) -> float:
    """Mean squared error between predicted probs and 0/1 labels."""
    if not preds:
        return float("nan")
    return sum((p - y) ** 2 for p, y in zip(preds, labels, strict=True)) / len(preds)


def _log_loss(preds: list[float], labels: list[int], *, eps: float = 1e-12) -> float:
    """Binary cross-entropy."""
    if not preds:
        return float("nan")
    s = 0.0
    for p, y in zip(preds, labels, strict=True):
        pp = min(1.0 - eps, max(eps, p))
        s += -y * math.log(pp) - (1 - y) * math.log(1.0 - pp)
    return s / len(preds)


def _running_z(
    values: list[float],
    current_idx: int,
) -> float | None:
    """Z-score of values[current_idx] against the mean/std of all prior values."""
    if current_idx < 1:
        return None
    prior = values[:current_idx]
    n = len(prior)
    mean = sum(prior) / n
    if n < 2:
        return None
    var = sum((x - mean) ** 2 for x in prior) / (n - 1)
    std = var**0.5
    if std < 1e-12:
        return None
    return (values[current_idx] - mean) / std


@dataclass(slots=True)
class RollingCalibration:
    """Rolling-window calibration evaluator.

    Splits outcomes into overlapping epochs of ``window`` trades, stepping
    by ``step`` trades each time. Computes Brier + log-loss per epoch and
    flags drift when the z-score exceeds ``drift_z``.

    Args:
        window: Trades per epoch.
        step: Trades to advance per epoch.
        drift_z: Z-score threshold for drift alert.
        min_epochs_for_z: Minimum prior epochs before z-score computed.
    """

    window: int = 60
    step: int = 30
    drift_z: float = 2.0
    min_epochs_for_z: int = 3

    def evaluate(
        self,
        outcomes: list[tuple[float, int]],
    ) -> list[EpochMetrics]:
        """Compute per-epoch calibration metrics.

        Args:
            outcomes: List of (predicted_score, label) pairs, ordered
                chronologically. ``predicted_score`` is the gauntlet
                composite score (0.0–1.0); ``label`` is 1 for a winning
                trade, 0 for a losing trade.

        Returns:
            List of EpochMetrics, one per rolling window.
        """
        n = len(outcomes)
        if n < self.window:
            # Not enough data for even one epoch
            if n == 0:
                return []
            preds = [o[0] for o in outcomes]
            labels = [o[1] for o in outcomes]
            return [
                EpochMetrics(
                    epoch_idx=0,
                    start=0,
                    end=n,
                    n=n,
                    brier=_brier(preds, labels),
                    log_loss=_log_loss(preds, labels),
                    base_rate=sum(labels) / n,
                    mean_pred=sum(preds) / n,
                    z_brier=None,
                    z_log_loss=None,
                    drift_alert=False,
                ),
            ]

        epochs: list[EpochMetrics] = []
        brier_history: list[float] = []
        ll_history: list[float] = []

        start = 0
        idx = 0
        while start + self.window <= n:
            end = start + self.window
            preds = [outcomes[i][0] for i in range(start, end)]
            labels = [outcomes[i][1] for i in range(start, end)]

            b = _brier(preds, labels)
            ll = _log_loss(preds, labels)
            base = sum(labels) / len(labels)
            mpred = sum(preds) / len(preds)

            brier_history.append(b)
            ll_history.append(ll)

            z_b = _running_z(brier_history, idx) if idx >= self.min_epochs_for_z else None
            z_ll = _running_z(ll_history, idx) if idx >= self.min_epochs_for_z else None

            drift = False
            if z_b is not None and abs(z_b) > self.drift_z:
                drift = True
            if z_ll is not None and abs(z_ll) > self.drift_z:
                drift = True

            epochs.append(
                EpochMetrics(
                    epoch_idx=idx,
                    start=start,
                    end=end,
                    n=len(labels),
                    brier=b,
                    log_loss=ll,
                    base_rate=base,
                    mean_pred=mpred,
                    z_brier=z_b,
                    z_log_loss=z_ll,
                    drift_alert=drift,
                )
            )

            start += self.step
            idx += 1

        return epochs


def rolling_calibration_report(
    epochs: list[EpochMetrics],
    *,
    title: str = "Rolling Calibration Report",
) -> str:
    """Render epoch metrics as a markdown report."""
    lines = [f"# {title}", ""]
    if not epochs:
        lines.append("_No epochs to report (insufficient data)._")
        return "\n".join(lines) + "\n"

    n_drift = sum(1 for e in epochs if e.drift_alert)
    lines.append(f"- Epochs: **{len(epochs)}**")
    lines.append(f"- Drift alerts: **{n_drift}**")
    briers = [e.brier for e in epochs if not math.isnan(e.brier)]
    if briers:
        lines.append(f"- Brier range: [{min(briers):.4f}, {max(briers):.4f}]")
        lines.append(f"- Brier mean: {sum(briers) / len(briers):.4f}")
    lines.append("")

    lines.append("## Epoch detail")
    lines.append("")
    lines.append(
        "| epoch | n | brier | log_loss | base_rate | mean_pred | z_brier | z_ll | drift |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|:---|")
    for e in epochs:
        zb = f"{e.z_brier:+.2f}" if e.z_brier is not None else "—"
        zl = f"{e.z_log_loss:+.2f}" if e.z_log_loss is not None else "—"
        flag = "**YES**" if e.drift_alert else ""
        lines.append(
            f"| {e.epoch_idx} | {e.n} | {e.brier:.4f} | {e.log_loss:.4f} "
            f"| {e.base_rate:.3f} | {e.mean_pred:.3f} | {zb} | {zl} | {flag} |"
        )
    lines.append("")

    if n_drift:
        lines.append("## Drift details")
        lines.append("")
        for e in epochs:
            if e.drift_alert:
                lines.append(
                    f"- **Epoch {e.epoch_idx}** (trades {e.start}–{e.end}): "
                    f"Brier z={e.z_brier:+.2f}, LogLoss z={e.z_log_loss:+.2f}"
                    if e.z_brier is not None and e.z_log_loss is not None
                    else f"- **Epoch {e.epoch_idx}** (trades {e.start}–{e.end}): drift detected"
                )
        lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "A drift alert means an epoch's Brier or log-loss deviates > 2σ from "
        "the running mean of prior epochs. This signals that the gauntlet gate "
        "scores may need recalibration — the relationship between predicted "
        "probabilities and realized outcomes has shifted."
    )

    return "\n".join(lines) + "\n"


__all__ = [
    "EpochMetrics",
    "RollingCalibration",
    "rolling_calibration_report",
]
