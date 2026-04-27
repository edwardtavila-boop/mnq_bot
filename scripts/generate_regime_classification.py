"""Generate ``reports/regime_classification.json`` for the v0.2.4
promotion gate (gate 6 -- regime_stability).

The gate evaluator at
``scripts/_promotion_gate.py::_gate_regime_stability`` consumes
``reports/regime_classification.json`` and reads ``losing_regimes``
(list of regime labels where the strategy had negative or zero
expectancy). The gate wants AT LEAST ONE losing regime as evidence
that the strategy has seen its own failure mode -- a strategy that
"never loses" in any regime is regime-cherrypicked and unsafe to
promote.

This script reuses the v0.2.12 regime classifier + v0.2.13 per-day
regime map. For a chosen variant (default: r5_real_wide_target),
it walks the variant's cached_backtest daily P&L, classifies each
day's regime, aggregates per-regime stats, and emits the list of
regimes where the variant had non-positive expectancy.

Usage
-----
    python scripts/generate_regime_classification.py
    python scripts/generate_regime_classification.py --variant r4_real_orderflow
    python scripts/generate_regime_classification.py --output /alt/path.json

Exit code: 0 always (this is a producer, not a gate).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mnq.spec.runtime_payload import build_spec_payload  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "reports" / "regime_classification.json"
DEFAULT_VARIANT = "r5_real_wide_target"

# A regime is "losing" when its per-trade expectancy_r is at or below
# this threshold. Even a 0-expectancy regime counts as losing for
# this gate's purpose (the test is "has the strategy seen failure?",
# not "does the strategy turn a profit?").
LOSING_THRESHOLD_R = 0.0


def build_classification(variant: str) -> dict:
    """Return the artifact payload for ``variant``.

    Shape:
        {
          "variant":          str,
          "regimes_seen":     [str, ...]   # all regimes with n_days >= 1
          "regimes_winning":  [str, ...]   # subset with expectancy_r > 0
          "losing_regimes":   [str, ...]   # subset with expectancy_r <= 0
          "regime_expectancy": dict         # full per-regime stats
                                            # (mirror of build_spec_payload)
          "_threshold_r":     float
        }
    """
    payload = build_spec_payload(variant)
    regime_exp = payload.get("regime_expectancy") or {}
    seen = sorted(regime for regime, stats in regime_exp.items() if stats.get("n_days", 0) > 0)
    winning = sorted(
        regime
        for regime, stats in regime_exp.items()
        if stats.get("n_days", 0) > 0 and stats.get("expectancy_r", 0.0) > LOSING_THRESHOLD_R
    )
    losing = sorted(
        regime
        for regime, stats in regime_exp.items()
        if stats.get("n_days", 0) > 0 and stats.get("expectancy_r", 0.0) <= LOSING_THRESHOLD_R
    )
    return {
        "variant": variant,
        "regimes_seen": seen,
        "regimes_winning": winning,
        "losing_regimes": losing,
        "regime_expectancy": regime_exp,
        "_threshold_r": LOSING_THRESHOLD_R,
        "provenance": payload.get("provenance") or [],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--variant",
        type=str,
        default=DEFAULT_VARIANT,
        help=f"variant to classify (default: {DEFAULT_VARIANT})",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output JSON artifact (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="suppress stdout summary",
    )
    args = p.parse_args(argv)

    payload = build_classification(args.variant)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    if not args.quiet:
        print(f"wrote {args.output}")
        print(
            f"summary: variant={payload['variant']} "
            f"seen={len(payload['regimes_seen'])} "
            f"winning={len(payload['regimes_winning'])} "
            f"losing={len(payload['losing_regimes'])}",
        )
        if payload["losing_regimes"]:
            print(
                "  losing regimes: " + ", ".join(payload["losing_regimes"]),
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
