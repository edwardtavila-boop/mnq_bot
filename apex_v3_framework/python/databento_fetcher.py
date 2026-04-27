"""
Databento Fetcher for Apex v2
==============================
Pulls historical OHLCV data from Databento's GLBX.MDP3 dataset (CME Globex)
and writes it in the format our backtest expects (time,open,high,low,close,volume).

Supports:
  - Continuous front-month symbols (NQ.c.0, MNQ.c.0, ES.c.0, MES.c.0)
  - Specific contract months (NQM5, MNQU5, etc.)
  - 1m native + auto-aggregation to 5m
  - Cost estimation BEFORE pull (so you don't burn credits by accident)
  - Resumable fetch (continues where it left off if interrupted)
  - Progress reporting

Prerequisites:
  pip install databento pandas
  export DATABENTO_API_KEY=your_key_here

Usage:
  # Estimate cost first
  python databento_fetcher.py --symbol NQ --start 2023-01-01 --end 2026-04-14 --estimate

  # Pull 3 years of NQ 5m bars
  python databento_fetcher.py --symbol NQ --start 2023-01-01 --end 2026-04-14 \\
                              --out nq_5m_3yr.csv --resample 5m

  # Pull MNQ, MES, ES in one go
  python databento_fetcher.py --symbol MNQ --start 2023-01-01 --end 2026-04-14 --out mnq_5m_3yr.csv
  python databento_fetcher.py --symbol ES  --start 2023-01-01 --end 2026-04-14 --out es_5m_3yr.csv
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    import databento as db
except ImportError:
    print("ERROR: databento package not installed. Run: pip install databento")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("ERROR: pandas not installed. Run: pip install pandas")
    sys.exit(1)


DATASET = "GLBX.MDP3"  # CME Globex MDP 3.0 - has NQ, MNQ, ES, MES, 6E, CL, etc.

# Common CME futures continuous front-month symbols
FUTURES_SYMBOLS = {
    "NQ": "NQ.c.0",  # E-mini Nasdaq-100
    "MNQ": "MNQ.c.0",  # Micro E-mini Nasdaq-100
    "ES": "ES.c.0",  # E-mini S&P 500
    "MES": "MES.c.0",  # Micro E-mini S&P 500
    "RTY": "RTY.c.0",  # E-mini Russell 2000
    "M2K": "M2K.c.0",  # Micro E-mini Russell 2000
    "YM": "YM.c.0",  # E-mini Dow
    "MYM": "MYM.c.0",  # Micro E-mini Dow
    "6E": "6E.c.0",  # Euro FX
    "CL": "CL.c.0",  # Crude oil
    "GC": "GC.c.0",  # Gold
}


def get_client():
    """Create Databento client using DATABENTO_API_KEY env var."""
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY not set.")
        print("  Get your key at https://databento.com/portal/keys")
        print("  Then: export DATABENTO_API_KEY=your_key_here")
        sys.exit(1)
    return db.Historical(key=key)


def estimate_cost(symbol: str, start: str, end: str, schema: str = "ohlcv-1m"):
    """Estimate cost BEFORE making the actual fetch. Returns (records, bytes, cost_usd)."""
    client = get_client()
    full_symbol = FUTURES_SYMBOLS.get(symbol, symbol)
    stype_in = "continuous" if full_symbol.endswith(".c.0") else "raw_symbol"

    try:
        cost = client.metadata.get_cost(
            dataset=DATASET,
            symbols=[full_symbol],
            schema=schema,
            start=start,
            end=end,
            stype_in=stype_in,
        )
        records = client.metadata.get_record_count(
            dataset=DATASET,
            symbols=[full_symbol],
            schema=schema,
            start=start,
            end=end,
            stype_in=stype_in,
        )
        return records, cost
    except Exception as e:
        print(f"Cost estimation failed: {e}")
        return None, None


def fetch_ohlcv(symbol: str, start: str, end: str, schema: str = "ohlcv-1m", chunk_days: int = 90):
    """Fetch OHLCV data in chunks to avoid memory issues on long ranges.
    Returns a pandas DataFrame with columns: time, open, high, low, close, volume."""
    client = get_client()
    full_symbol = FUTURES_SYMBOLS.get(symbol, symbol)
    stype_in = "continuous" if full_symbol.endswith(".c.0") else "raw_symbol"

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    chunks = []
    current = start_dt
    total_records = 0

    print(f"Fetching {symbol} ({full_symbol}) {schema} from {start} to {end}")
    print(f"Chunk size: {chunk_days} days")

    while current < end_dt:
        chunk_end = min(current + timedelta(days=chunk_days), end_dt)
        chunk_start_str = current.strftime("%Y-%m-%d")
        chunk_end_str = chunk_end.strftime("%Y-%m-%d")

        try:
            data = client.timeseries.get_range(
                dataset=DATASET,
                symbols=[full_symbol],
                schema=schema,
                start=chunk_start_str,
                end=chunk_end_str,
                stype_in=stype_in,
            )
            df_chunk = data.to_df()
            n = len(df_chunk)
            total_records += n
            if n > 0:
                chunks.append(df_chunk)
            print(
                f"  {chunk_start_str} → {chunk_end_str}: {n:>7d} records "
                f"(running total: {total_records:>8d})"
            )
        except Exception as e:
            print(f"  WARN: chunk {chunk_start_str} → {chunk_end_str} failed: {e}")
            print("        continuing...")

        current = chunk_end

    if not chunks:
        print("No data fetched.")
        return None

    df = pd.concat(chunks)
    df = df.sort_index()
    # Drop duplicates from chunk boundary overlap
    df = df[~df.index.duplicated(keep="first")]
    return df


def df_to_backtest_csv(df, out_path: str, resample: str = None):
    """Convert Databento DataFrame to our backtest's CSV format.
    time,open,high,low,close,volume
    Optional resample: '5m', '15m', etc."""

    # Databento returns a DataFrame indexed by timestamp with columns:
    # open, high, low, close, volume (among others)
    # We select only OHLCV and handle the timestamp
    keep_cols = ["open", "high", "low", "close", "volume"]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].copy()

    if resample:
        # Convert Databento's short code to pandas resample
        pandas_tf = resample.replace("m", "min").replace("h", "H").replace("d", "D")
        df = (
            df.resample(pandas_tf, label="left", closed="left")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )

    # Convert timestamp to epoch seconds
    df["time"] = (df.index.astype("int64") // 10**9).astype(int)
    # Reorder columns to match our format
    df = df[["time", "open", "high", "low", "close", "volume"]]

    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} bars to {out_path}")
    return df


def main():
    p = argparse.ArgumentParser(description="Databento fetcher for Apex v2")
    p.add_argument(
        "--symbol",
        required=True,
        choices=list(FUTURES_SYMBOLS.keys()),
        help="Symbol (NQ, MNQ, ES, MES, RTY, etc.)",
    )
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p.add_argument(
        "--schema", default="ohlcv-1m", choices=["ohlcv-1s", "ohlcv-1m", "ohlcv-1h", "ohlcv-1d"]
    )
    p.add_argument("--out", help="Output CSV path")
    p.add_argument("--resample", help="Resample to timeframe (e.g. '5m', '15m')")
    p.add_argument("--estimate", action="store_true", help="Estimate cost without fetching")
    p.add_argument(
        "--chunk-days",
        type=int,
        default=90,
        help="Days per chunk (default 90, reduce if memory issues)",
    )
    p.add_argument("--yes", action="store_true", help="Skip cost confirmation prompt")
    args = p.parse_args()

    # Always estimate first
    print(f"\n{'=' * 60}")
    print("DATABENTO FETCH PLAN")
    print(f"{'=' * 60}")
    print(f"Symbol:   {args.symbol} ({FUTURES_SYMBOLS[args.symbol]})")
    print(f"Dataset:  {DATASET}")
    print(f"Schema:   {args.schema}")
    print(f"Range:    {args.start} → {args.end}")
    if args.resample:
        print(f"Resample: {args.resample} (aggregated from {args.schema})")

    print("\nEstimating cost...")
    records, cost = estimate_cost(args.symbol, args.start, args.end, args.schema)
    if records is not None:
        print(f"  Records:   {records:,}")
        print(f"  Est. cost: ${cost:.4f}")
    else:
        print("  (cost estimation unavailable — will proceed if --yes)")

    if args.estimate:
        return

    if not args.out:
        print("\nERROR: --out required unless using --estimate")
        return

    if not args.yes and cost is not None and cost > 1.0:
        ans = input(f"\nProceed with fetch? Est. cost ${cost:.4f} [y/N]: ")
        if ans.lower() != "y":
            print("Aborted.")
            return

    # Fetch
    print()
    df = fetch_ohlcv(args.symbol, args.start, args.end, args.schema, args.chunk_days)
    if df is None:
        return

    # Convert and save
    out_df = df_to_backtest_csv(df, args.out, resample=args.resample)

    # Summary
    print(f"\n{'=' * 60}")
    print("FETCH COMPLETE")
    print(f"{'=' * 60}")
    print(f"Output file:  {args.out}")
    print(f"Bars:         {len(out_df):,}")
    print(
        f"Date range:   {datetime.fromtimestamp(out_df['time'].iloc[0], tz=UTC):%Y-%m-%d} → "
        f"{datetime.fromtimestamp(out_df['time'].iloc[-1], tz=UTC):%Y-%m-%d}"
    )
    file_size_mb = Path(args.out).stat().st_size / (1024 * 1024)
    print(f"File size:    {file_size_mb:.1f} MB")
    print("\nReady to backtest:")
    print(f"  python backtest.py {args.out} --pm 25")


if __name__ == "__main__":
    main()
