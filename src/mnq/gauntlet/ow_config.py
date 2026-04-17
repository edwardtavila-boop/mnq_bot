"""Outcome-weighted gate config loader — loads OW weights for the hard-gate.

Batch 11B. Provides a single function to build a ``GauntletHardGateConfig``
from the serialized outcome weights at ``data/outcome_gate_weights.json``.

Also provides a walk-forward OW weight estimator that can be called
periodically to retrain weights as new data accumulates.

Usage:

    from mnq.gauntlet.ow_config import load_ow_config, OW_WEIGHTS_PATH

    cfg = load_ow_config()
    # cfg is a GauntletHardGateConfig with gate_weights populated
    # Falls back to raw pass_rate if file missing or invalid

    # Or build from explicit weights:
    cfg = build_ow_config({"cross_mag": 0.073, "session": 0.019})
"""
from __future__ import annotations

import json
from pathlib import Path

from mnq.gauntlet.hard_gate import GauntletHardGateConfig

__all__ = [
    "OW_WEIGHTS_PATH",
    "build_ow_config",
    "load_ow_config",
]

# Default path — relative to repo root. Caller can override.
OW_WEIGHTS_PATH = Path(__file__).resolve().parents[3] / "data" / "outcome_gate_weights.json"


def load_ow_config(
    weights_path: Path | None = None,
    *,
    skip_threshold: float = 0.50,
    reduce_threshold: float = 0.67,
    min_weight: float = 0.001,
    fallback_raw: bool = True,
) -> GauntletHardGateConfig:
    """Load outcome-weighted gate config from JSON file.

    Parameters
    ----------
    weights_path:
        Path to the JSON file. Defaults to ``data/outcome_gate_weights.json``.
    skip_threshold:
        Pass-rate below which the trade is skipped entirely.
    reduce_threshold:
        Pass-rate below which size is reduced.
    min_weight:
        Gates with weight below this are treated as zero (noise floor).
    fallback_raw:
        If True and the file is missing/invalid, return a config with
        no gate_weights (falls back to raw pass_rate). If False, raise.

    Returns
    -------
    GauntletHardGateConfig with gate_weights populated from the file.
    """
    path = weights_path or OW_WEIGHTS_PATH

    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        if fallback_raw:
            return GauntletHardGateConfig(
                skip_threshold=skip_threshold,
                reduce_threshold=reduce_threshold,
                critical_gates=frozenset(),
                gate_weights=None,
            )
        raise

    raw_weights = data.get("gate_weights", {})
    if not isinstance(raw_weights, dict):
        if fallback_raw:
            return GauntletHardGateConfig(
                skip_threshold=skip_threshold,
                reduce_threshold=reduce_threshold,
                critical_gates=frozenset(),
                gate_weights=None,
            )
        msg = f"Invalid gate_weights in {path}"
        raise ValueError(msg)

    # Filter to weights above noise floor
    gate_weights = {
        name: float(w) for name, w in raw_weights.items()
        if float(w) >= min_weight
    }

    return GauntletHardGateConfig(
        skip_threshold=skip_threshold,
        reduce_threshold=reduce_threshold,
        critical_gates=frozenset(),  # OW mode doesn't use critical gates
        gate_weights=gate_weights if gate_weights else None,
    )


def build_ow_config(
    gate_weights: dict[str, float],
    *,
    skip_threshold: float = 0.50,
    reduce_threshold: float = 0.67,
    min_weight: float = 0.001,
) -> GauntletHardGateConfig:
    """Build config from explicit gate weights dict.

    Convenience function for testing or when weights come from
    a walk-forward retrain rather than a static JSON file.
    """
    filtered = {
        name: w for name, w in gate_weights.items()
        if w >= min_weight
    }
    return GauntletHardGateConfig(
        skip_threshold=skip_threshold,
        reduce_threshold=reduce_threshold,
        critical_gates=frozenset(),
        gate_weights=filtered if filtered else None,
    )


def retrain_and_save(
    records: list,
    weights_path: Path | None = None,
    *,
    min_samples: int = 5,
) -> GauntletHardGateConfig:
    """Retrain OW weights from fresh records and save to JSON.

    Parameters
    ----------
    records:
        List of ``GateDayRecord`` objects for training.
    weights_path:
        Path to save the JSON file. Defaults to ``data/outcome_gate_weights.json``.
    min_samples:
        Minimum samples per gate bucket.

    Returns
    -------
    The new GauntletHardGateConfig with updated weights.
    """
    from mnq.gauntlet.outcome_weights import compute_gate_weights

    weights = compute_gate_weights(records, min_samples=min_samples)

    path = weights_path or OW_WEIGHTS_PATH
    data = {
        "method": weights.method,
        "n_days": weights.n_days,
        "total_pnl": weights.total_pnl,
        "gate_weights": weights.gate_weights,
        "gate_details": [
            {
                "name": r.name,
                "weight": round(r.weight, 4),
                "raw_correlation": round(r.raw_correlation, 4),
                "pass_pnl_mean": round(r.pass_pnl_mean, 2),
                "fail_pnl_mean": round(r.fail_pnl_mean, 2),
                "pass_count": r.pass_count,
                "fail_count": r.fail_count,
                "information_value": round(r.information_value, 4),
            }
            for r in sorted(weights.gate_results, key=lambda x: -x.weight)
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")

    return load_ow_config(path)
