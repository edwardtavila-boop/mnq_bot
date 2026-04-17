"""Rolling calibration report across multi-day Databento runs.

Batch 7C. Reads all available daily results, constructs outcome pairs
from the gauntlet score + trade P/L, and runs the rolling calibration
evaluator.

Usage:

    python scripts/rolling_calibration.py \\
        --output reports/rolling_calibration.md

Self-contained — no desktop_app/firm imports.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.gauntlet.rolling_calibration import (  # noqa: E402
    RollingCalibration,
    rolling_calibration_report,
)

DEFAULT_OUTPUT = REPO_ROOT / "reports" / "rolling_calibration.md"
DATA_DIR = REPO_ROOT / "data" / "real_mnq_1m_rth"


def _load_outcomes_from_reports() -> list[tuple[float, int]]:
    """Scan reports directory for daily run data.

    Falls back to synthetic outcomes from available data if no journal
    with gauntlet scores exists.

    Returns list of (gauntlet_composite_score, win_label) pairs.
    """
    # Try reading from the run_all_phases report for day-level results
    report_path = REPO_ROOT / "reports" / "run_all_phases.md"
    outcomes: list[tuple[float, int]] = []

    if not report_path.exists():
        return outcomes

    # Parse day results from the report — look for gauntlet pass_rate + PnL
    # In a real multi-year run these come from the journal; here we
    # reconstruct from what the orchestrator recorded.
    try:
        text = report_path.read_text()
        # The orchestrator logs per-day gauntlet scores + trade outcomes
        # For now, extract what we can; fall back to generating from bars
        for line in text.splitlines():
            # Look for lines with gauntlet_score and pnl data
            if "gauntlet_score=" in line and "pnl=" in line:
                parts = {
                    k: v
                    for item in line.split()
                    if "=" in item
                    for k, v in [item.split("=", 1)]
                }
                if "gauntlet_score" in parts and "pnl" in parts:
                    try:
                        score = float(parts["gauntlet_score"])
                        pnl = float(parts["pnl"])
                        outcomes.append((score, 1 if pnl > 0 else 0))
                    except ValueError:
                        continue
    except Exception:
        pass

    return outcomes


def _generate_synthetic_outcomes() -> list[tuple[float, int]]:
    """Generate outcomes from available day files using gauntlet scoring.

    Runs the gauntlet on each day's bars at the peak-volume bar and
    uses the day's net close-to-close return as the label.
    """
    from mnq.core.types import Bar
    from mnq.gauntlet.day_aggregate import gauntlet_day_score

    day_files = sorted(DATA_DIR.glob("*.parquet"))
    if not day_files:
        return []

    outcomes: list[tuple[float, int]] = []

    try:
        import polars as pl
    except ImportError:
        return outcomes

    for day_file in day_files:
        try:
            df = pl.read_parquet(day_file)
            if df.is_empty() or len(df) < 5:
                continue

            # Build bars from parquet
            bars: list[Bar] = []
            for row in df.iter_rows(named=True):
                from datetime import datetime
                from decimal import Decimal

                ts = row.get("ts") or row.get("timestamp") or row.get("time")
                if ts is None:
                    continue
                if isinstance(ts, (int, float)):
                    ts = datetime.fromtimestamp(ts, tz=UTC)

                bars.append(
                    Bar(
                        ts=ts,
                        open=Decimal(str(row.get("open", 0))),
                        high=Decimal(str(row.get("high", 0))),
                        low=Decimal(str(row.get("low", 0))),
                        close=Decimal(str(row.get("close", 0))),
                        volume=int(row.get("volume", 0)),
                        timeframe_sec=60,
                    )
                )

            if len(bars) < 5:
                continue

            # Score this day
            day = gauntlet_day_score(bars, side="long")
            # Use pass_rate as the predicted probability
            pred = day.pass_rate
            # Label: 1 if day net positive (close > open of first bar)
            label = 1 if float(bars[-1].close) > float(bars[0].open) else 0
            outcomes.append((pred, label))

        except Exception:
            continue

    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rolling calibration report (Batch 7C).")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--step", type=int, default=30)
    parser.add_argument("--drift-z", type=float, default=2.0)
    args = parser.parse_args(argv)

    # Try real outcomes first, fall back to synthetic
    outcomes = _load_outcomes_from_reports()
    source = "journal"
    if not outcomes:
        outcomes = _generate_synthetic_outcomes()
        source = "synthetic (gauntlet on Databento bars)"
    if not outcomes:
        # Generate a minimal demonstration
        print("No outcome data found. Generating stub report.")
        outcomes = [(0.5, 1), (0.5, 0)] * 30
        source = "stub (no data available)"

    print(f"Rolling calibration: {len(outcomes)} outcomes from {source}")

    cal = RollingCalibration(
        window=args.window,
        step=args.step,
        drift_z=args.drift_z,
    )
    epochs = cal.evaluate(outcomes)

    md = rolling_calibration_report(
        epochs,
        title=f"Rolling Calibration — {source} ({len(outcomes)} trades)",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
