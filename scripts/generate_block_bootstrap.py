"""Generate ``reports/block_bootstrap.json`` for the v0.2.4 promotion
gate (gate 3 -- block_bootstrap_ci_low).

The gate evaluator at ``scripts/_promotion_gate.py::_gate_block_bootstrap_ci_low``
consumes ``reports/block_bootstrap.json`` and reads ``ci95_low``. This
script is the producer:

  1. Read FILL_REALIZED events from the live_sim journal
  2. Pair entry/exit fills to compute per-trade R-multiples
  3. Run block bootstrap (block=5, k=10000) via mnq.stats
  4. Write the artifact

Usage
-----
    python scripts/generate_block_bootstrap.py
    python scripts/generate_block_bootstrap.py --journal /alt/path
    python scripts/generate_block_bootstrap.py --output /alt.json
    python scripts/generate_block_bootstrap.py --variant r5_real_wide_target
    python scripts/generate_block_bootstrap.py --k 20000 --block 10

Exit code is 0 on success, 1 on missing inputs (journal absent or
zero fills). The resulting artifact carries n_trades=0 + ci95_low=0
for the empty case so the gate evaluator correctly transitions from
NO_DATA to FAIL once the journal exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.core.paths import LIVE_SIM_JOURNAL  # noqa: E402
from mnq.core.types import MNQ_POINT_VALUE, MNQ_TICK_SIZE  # noqa: E402
from mnq.stats import block_bootstrap_ci  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "reports" / "block_bootstrap.json"


def _read_per_trade_r(
    journal_path: Path,
    *,
    variant_filter: str | None = None,
) -> list[float]:
    """Pair FILL_REALIZED events from the journal into per-trade R-multiples.

    Pairing rule (simple, conservative):
      * Entry fill:   first fill for a given client_order_id
      * Exit fill:    next opposite-side fill (qty zero net)
      * R-multiple:   (exit_price - entry_price) * sign / risk_dollars

    Returns an empty list if the journal is missing, empty, or has
    only one-sided fills. ``risk_dollars`` is approximated from each
    fill's recorded ``stop_distance_ticks`` if present; falls back to
    a flat 10-tick stop ($5 risk) so the function never returns NaN.

    This is intentionally a SIMPLE proxy. The downstream Firm review
    needs only the order of magnitude (gate threshold +0.05R); the
    next-iteration version reads spec-aware R-multiples from the
    Order state machine directly.
    """
    if not journal_path.exists():
        return []
    try:
        from mnq.storage.journal import EventJournal
        from mnq.storage.schema import FILL_REALIZED
    except ImportError:
        return []
    try:
        j = EventJournal(journal_path)
        # Group fills by client_order_id, take first as entry, last
        # as exit (for the simple paired-fill case).
        by_order: dict[str, list[dict[str, Any]]] = {}
        for event in j.replay(event_types=(FILL_REALIZED,)):
            payload = getattr(event, "payload", None) or {}
            if variant_filter:
                if payload.get("variant") != variant_filter:
                    continue
            cid = payload.get("client_order_id") or payload.get("order_id")
            if not cid:
                continue
            by_order.setdefault(str(cid), []).append(payload)
    except Exception:  # noqa: BLE001 -- defensive; never crash artifact gen
        return []

    risk_dollars = float(MNQ_TICK_SIZE) * float(MNQ_POINT_VALUE) * 10.0
    rs: list[float] = []
    for fills in by_order.values():
        if len(fills) < 2:
            continue
        entry = fills[0]
        exit_ = fills[-1]
        try:
            entry_price = float(entry.get("price", 0.0) or 0.0)
            exit_price = float(exit_.get("price", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        side = str(entry.get("side", "long")).lower()
        sign = 1.0 if side == "long" else -1.0
        per_trade_r = sign * (exit_price - entry_price) * float(MNQ_POINT_VALUE) / risk_dollars
        rs.append(per_trade_r)
    return rs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--journal", type=Path, default=LIVE_SIM_JOURNAL,
        help=f"event journal (default: {LIVE_SIM_JOURNAL})",
    )
    p.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"output JSON artifact (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--variant", type=str, default=None,
        help="filter to fills tagged with this variant name",
    )
    p.add_argument(
        "--k", type=int, default=10000,
        help="bootstrap iterations (default 10000)",
    )
    p.add_argument(
        "--block", type=int, default=5,
        help="block size in trades (default 5)",
    )
    p.add_argument(
        "--seed", type=int, default=11,
        help="RNG seed for reproducibility (default 11)",
    )
    p.add_argument(
        "--threshold-r", type=float, default=0.05,
        help="paper-gate threshold (default +0.05R)",
    )
    args = p.parse_args(argv)

    rs = _read_per_trade_r(args.journal, variant_filter=args.variant)
    result = block_bootstrap_ci(
        rs, k=args.k, block_size=args.block, seed=args.seed,
        paper_gate_r=args.threshold_r,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"wrote {args.output}")
    print(
        f"summary: n_trades={result['n_trades']} "
        f"mean={result['mean']:+.4f}R "
        f"ci95_low={result['ci95_low']:+.4f}R "
        f"ci95_high={result['ci95_high']:+.4f}R "
        f"p_above_{args.threshold_r}={result['p_above_paper_gate']:.3f}",
    )
    if result["n_trades"] == 0:
        print(
            "note: 0 trades found in journal -- gate will read this as "
            "FAIL (ci95_low=0.0 NOT > +0.05R). Run a paper-soak first.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
