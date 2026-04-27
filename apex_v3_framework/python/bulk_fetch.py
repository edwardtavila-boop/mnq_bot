"""
Bulk Databento Pull - All Apex v2 Symbols
==========================================
Single command to pull years of NQ, MNQ, ES, and MES 5m bars.
Outputs CSVs ready for backtest.py and master_test.py.

Prerequisites:
  pip install databento pandas
  export DATABENTO_API_KEY=your_key_here

Usage:
  python bulk_fetch.py --start 2023-01-01 --end 2026-04-14 --out-dir ./historical
  python bulk_fetch.py --start 2023-01-01 --end 2026-04-14 --out-dir ./historical --estimate

This will create:
  ./historical/nq_5m.csv
  ./historical/mnq_5m.csv
  ./historical/es_5m.csv
  ./historical/mes_5m.csv
"""

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_SYMBOLS = ["NQ", "MNQ", "ES", "MES"]


def main():
    p = argparse.ArgumentParser(description="Bulk Databento pull")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--out-dir", default="./historical", help="Output directory")
    p.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="Symbols to pull (default: NQ MNQ ES MES)",
    )
    p.add_argument(
        "--resample", default="5m", help="Target timeframe (default 5m, aggregated from 1m)"
    )
    p.add_argument("--estimate", action="store_true", help="Show cost estimates only, no fetch")
    p.add_argument("--yes", action="store_true", help="Skip confirmations")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    script_path = Path(__file__).parent / "databento_fetcher.py"

    # Cost estimation pass
    print("=" * 72)
    print(f"BULK FETCH PLAN: {len(args.symbols)} symbols, {args.start} → {args.end}")
    print("=" * 72)

    for sym in args.symbols:
        out_file = out_dir / f"{sym.lower()}_{args.resample}.csv"
        print(f"\n{'─' * 72}")
        print(f"{sym} → {out_file}")
        print(f"{'─' * 72}")
        cmd = [
            sys.executable,
            str(script_path),
            "--symbol",
            sym,
            "--start",
            args.start,
            "--end",
            args.end,
            "--schema",
            "ohlcv-1m",
            "--estimate",
        ]
        subprocess.run(cmd)

    if args.estimate:
        print(f"\n{'=' * 72}")
        print("ESTIMATE ONLY - no data fetched. Remove --estimate to proceed.")
        print(f"{'=' * 72}")
        return

    # Full fetch
    print(f"\n{'=' * 72}")
    print("PROCEEDING WITH FULL FETCH")
    print(f"{'=' * 72}")
    if not args.yes:
        ans = input("Continue with full fetch of all symbols? [y/N]: ")
        if ans.lower() != "y":
            print("Aborted.")
            return

    for sym in args.symbols:
        out_file = out_dir / f"{sym.lower()}_{args.resample}.csv"
        print(f"\n{'=' * 72}")
        print(f"FETCHING {sym} → {out_file}")
        print(f"{'=' * 72}")
        cmd = [
            sys.executable,
            str(script_path),
            "--symbol",
            sym,
            "--start",
            args.start,
            "--end",
            args.end,
            "--schema",
            "ohlcv-1m",
            "--out",
            str(out_file),
            "--resample",
            args.resample,
            "--yes",
        ]
        subprocess.run(cmd)

    print(f"\n{'=' * 72}")
    print("BULK FETCH COMPLETE")
    print(f"{'=' * 72}")
    print(f"Output directory: {out_dir}")
    for sym in args.symbols:
        out_file = out_dir / f"{sym.lower()}_{args.resample}.csv"
        if out_file.exists():
            size_mb = out_file.stat().st_size / (1024 * 1024)
            print(f"  ✓ {out_file}  ({size_mb:.1f} MB)")

    print("\nNext steps:")
    print("  # Run full backtest on historical NQ data")
    print(f"  python backtest.py {out_dir}/nq_5m.csv --pm 25 --es {out_dir}/es_5m.csv")
    print("\n  # Monte Carlo with the new sample")
    print(f"  python monte_carlo.py {out_dir}/nq_5m.csv --pm 25 --sims 2000")
    print("\n  # Walk-forward with auto PM sweep")
    print(f"  python walkforward.py {out_dir}/nq_5m.csv --windows 12 --sweep")


if __name__ == "__main__":
    main()
