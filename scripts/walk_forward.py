"""Walk-forward rolling-window optimizer.

Phase 3+ / cross-cutting. The A/B harness ranks variants **in-sample** on
all 15 days, which systematically overstates edge. A walk-forward schedule
forces every evaluation to be out-of-sample:

    train window [i .. i+W_train)  →  pick best variant  →
    test window  [i+W_train .. i+W_train+W_test)  →  record test PnL

Sliding the window forward by ``stride`` gives us many (train, test) folds.
Aggregating the test-window PnL across folds is an honest edge estimate:
the variant picked in one fold never sees its own fold's test data.

Usage:

    python scripts/walk_forward.py                       # defaults: 10/3/1
    python scripts/walk_forward.py --train 8 --test 3    # custom window
    python scripts/walk_forward.py --variants r5_real_wide_target t16_r5_long_only
    python scripts/walk_forward.py --output reports/walk_forward.md
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from strategy_ab import _load_real_days, _run_variant  # noqa: E402
from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

from mnq.spec.loader import load_spec  # noqa: E402

# strategy_v2 exposes VARIANTS as a list[StrategyConfig]; re-key by name
# so walk-forward can look variants up by label.
VARIANTS: dict[str, object] = {cfg.name: cfg for cfg in _VARIANT_LIST}

BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "walk_forward.md"


@dataclass
class FoldResult:
    fold_ix: int
    train_range: tuple[int, int]
    test_range: tuple[int, int]
    winner_name: str
    train_pnl: float
    test_pnl: float
    test_n_trades: int
    test_win_rate: float


def _pick_best_on_train(
    variants: list[str], spec, train_days
) -> tuple[str, float]:
    """Score each variant on the train slice; return (name, pnl)."""
    best_name = ""
    best_pnl = -1e18
    for name in variants:
        cfg = VARIANTS.get(name)
        if cfg is None:
            continue
        result = _run_variant(cfg, spec, train_days, seed=0)
        pnl = float(result.net_pnl)
        # Same small-n penalty as strategy_ab; we don't want to "win"
        # by producing 2 trades in the train set.
        if result.n_trades < 4:
            pnl -= 100.0
        if pnl > best_pnl:
            best_pnl = pnl
            best_name = name
    return best_name, best_pnl


def walk_forward(
    *,
    train_window: int = 10,
    test_window: int = 3,
    stride: int = 1,
    variants: list[str] | None = None,
    timeframe: str = "1m",
) -> list[FoldResult]:
    """Run the walk-forward schedule; return per-fold results."""
    spec = load_spec(BASELINE)
    days = _load_real_days(timeframe=timeframe)
    n = len(days)
    if n < train_window + test_window:
        raise RuntimeError(
            f"not enough days: have {n}, need >= {train_window + test_window}"
        )

    # Variant pool (default: everything in VARIANTS)
    pool = list(variants) if variants else list(VARIANTS.keys())

    folds: list[FoldResult] = []
    fold_ix = 0
    i = 0
    while i + train_window + test_window <= n:
        train_slice = days[i : i + train_window]
        test_slice = days[i + train_window : i + train_window + test_window]

        winner_name, train_pnl = _pick_best_on_train(pool, spec, train_slice)
        if not winner_name:
            break

        # Evaluate winner on test slice
        cfg = VARIANTS[winner_name]
        test_result = _run_variant(cfg, spec, test_slice, seed=0)

        folds.append(
            FoldResult(
                fold_ix=fold_ix,
                train_range=(i, i + train_window),
                test_range=(i + train_window, i + train_window + test_window),
                winner_name=winner_name,
                train_pnl=float(train_pnl),
                test_pnl=float(test_result.net_pnl),
                test_n_trades=test_result.n_trades,
                test_win_rate=test_result.win_rate,
            )
        )

        fold_ix += 1
        i += stride
    return folds


def _render_report(folds: list[FoldResult], *, train: int, test: int, stride: int) -> str:
    lines: list[str] = ["# Walk-Forward Optimizer Report", ""]
    lines.append(f"- train window: **{train}** days")
    lines.append(f"- test window: **{test}** days")
    lines.append(f"- stride: **{stride}** day(s)")
    lines.append(f"- folds: **{len(folds)}**")
    lines.append("")

    if not folds:
        lines.append("_No folds produced — data set too small for this window pair._")
        return "\n".join(lines) + "\n"

    # Aggregate
    test_pnls = [f.test_pnl for f in folds]
    total_test = sum(test_pnls)
    total_trades = sum(f.test_n_trades for f in folds)
    avg_test = total_test / len(folds)
    std_test = statistics.stdev(test_pnls) if len(folds) > 1 else 0.0
    pos_folds = sum(1 for p in test_pnls if p > 0)

    lines.append("## Aggregate out-of-sample edge")
    lines.append("")
    lines.append(f"- total test PnL across folds: **${total_test:+,.2f}**")
    lines.append(f"- total test trades: **{total_trades}**")
    lines.append(f"- mean test PnL per fold: **${avg_test:+,.2f}**")
    lines.append(f"- stdev test PnL per fold: ${std_test:,.2f}")
    lines.append(f"- positive folds: **{pos_folds}/{len(folds)}**")
    lines.append("")

    # Per-fold table
    lines.append("## Per-fold ledger")
    lines.append("")
    lines.append(
        "| Fold | Train range | Test range | Train winner | Train PnL | Test PnL | Test n | Test WR |"
    )
    lines.append("|---:|---|---|---|---:|---:|---:|---:|")
    for f in folds:
        lines.append(
            f"| {f.fold_ix} | {f.train_range[0]}–{f.train_range[1]} | "
            f"{f.test_range[0]}–{f.test_range[1]} | `{f.winner_name}` | "
            f"${f.train_pnl:+,.2f} | ${f.test_pnl:+,.2f} | "
            f"{f.test_n_trades} | {f.test_win_rate:.1%} |"
        )
    lines.append("")

    # Winner stability
    pick_counts: dict[str, int] = {}
    for f in folds:
        pick_counts[f.winner_name] = pick_counts.get(f.winner_name, 0) + 1
    lines.append("## Winner stability")
    lines.append("")
    lines.append("| Variant | Fold wins |")
    lines.append("|---|---:|")
    for name, k in sorted(pick_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{name}` | {k} |")
    lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "* If the same variant wins most folds, the signal is stable — that's a "
        "candidate for Firm review and the falsification pipeline."
    )
    lines.append(
        "* If the fold winner rotates every window, either the tape is regime-"
        "heterogeneous (in which case we need a per-regime ensemble) or every "
        "variant is statistical noise."
    )
    lines.append(
        "* Out-of-sample mean per-fold PnL is the honest edge estimate. If it is "
        "negative or within one stdev of zero, no variant has earned the right to "
        "ship to shadow trading."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward optimizer.")
    parser.add_argument("--train", type=int, default=10, dest="train_window")
    parser.add_argument("--test", type=int, default=3, dest="test_window")
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--timeframe", type=str, default="1m")
    parser.add_argument(
        "--variants",
        nargs="*",
        default=None,
        help="Subset of variant names to consider (default: all).",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    folds = walk_forward(
        train_window=args.train_window,
        test_window=args.test_window,
        stride=args.stride,
        variants=args.variants,
        timeframe=args.timeframe,
    )
    md = _render_report(folds, train=args.train_window, test=args.test_window, stride=args.stride)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    _ = Decimal  # keep import live for future use
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
