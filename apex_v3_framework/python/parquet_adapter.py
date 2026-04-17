"""
Parquet → CSV adapter for the Apex v3 backtest/simulator.

The mnq_backtest cache at
    <OneDrive>/Desktop/Base/mnq_backtest/.cache/parquet/
contains DataBento-sourced OHLCV parquet files with ~7 years of data
per symbol. Columns: ts_utc (int64 ns), open, high, low, close, volume,
session (ETH/RTH/CLOSED), symbol, tf.

The existing backtest.py and simulator expect CSV with columns:
    time,open,high,low,close,volume
where `time` is a unix epoch second.

This adapter converts the parquet cache into that CSV format, filters by
session if requested, and writes to data/bars/databento/*.csv.

Usage:
    .venv/bin/python eta_v3_framework/python/parquet_adapter.py --symbol MNQ1 --tf 5m
    .venv/bin/python eta_v3_framework/python/parquet_adapter.py --all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PARQUET_ROOT = Path(
    "/sessions/kind-keen-faraday/mnt/OneDrive/Desktop/Base/mnq_backtest/.cache/parquet"
)
CSV_OUT_ROOT = Path(
    "/sessions/kind-keen-faraday/mnt/mnq_bot/data/bars/databento"
)

DEFAULT_SYMBOLS = [
    ("MNQ1", "5m"),   # primary execution instrument
    ("MNQ1", "1m"),   # microstructure entries
    ("NQ1", "4h"),    # HTF bias
    ("ES_1m", None),  # V9 ES correlation (special naming)
    ("ES", "5m"),
    ("DXY", "5m"),    # V10 macro
    ("VIX_YF", "D"),  # V8 VIX (daily only in cache)
]


def parquet_to_csv(symbol: str, tf: str, session_filter: str | None = None) -> tuple[Path, int]:
    """Convert one parquet file → CSV in the backtest-expected shape.

    Returns (output_path, row_count).
    """
    # Handle the naming conventions in the cache
    if tf is None:
        stem = symbol  # e.g., ES_1m
    else:
        stem = f"{symbol}_{tf}"

    parquet_path = PARQUET_ROOT / f"{stem}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"Parquet not found: {parquet_path}")

    df = pd.read_parquet(parquet_path)

    if session_filter:
        df = df[df.session == session_filter].copy()

    # Convert ts_utc (int64 nanoseconds) → unix seconds
    df["time"] = (df["ts_utc"] // 1_000_000_000).astype("int64")

    out_df = df[["time", "open", "high", "low", "close", "volume"]].copy()
    out_df = out_df.sort_values("time").reset_index(drop=True)

    CSV_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    csv_path = CSV_OUT_ROOT / f"{stem.lower()}.csv"
    out_df.to_csv(csv_path, index=False)
    return csv_path, len(out_df)


def main() -> int:
    ap = argparse.ArgumentParser(description="Parquet → CSV adapter")
    ap.add_argument("--symbol", help="Symbol (MNQ1, NQ1, ES, DXY, VIX_YF)")
    ap.add_argument("--tf", help="Timeframe (1m, 5m, 4h, D)")
    ap.add_argument("--session", choices=["ETH", "RTH", "CLOSED"],
                    help="Optional session filter")
    ap.add_argument("--all", action="store_true", help="Convert all default symbols")
    args = ap.parse_args()

    if not args.all and not args.symbol:
        ap.print_help()
        return 2

    if args.all:
        print(f"{'SYMBOL':<12} {'ROWS':>10} {'OUTPUT':<60}")
        print("-" * 84)
        total_rows = 0
        for sym, tf in DEFAULT_SYMBOLS:
            try:
                path, rows = parquet_to_csv(sym, tf, args.session)
                label = f"{sym}_{tf}" if tf else sym
                print(f"{label:<12} {rows:>10,} {str(path.name):<60}")
                total_rows += rows
            except FileNotFoundError as e:
                print(f"{sym}_{tf}: MISSING ({e})")
        print("-" * 84)
        print(f"{'TOTAL':<12} {total_rows:>10,} rows written")
    else:
        path, rows = parquet_to_csv(args.symbol, args.tf, args.session)
        print(f"wrote {rows:,} rows to {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
