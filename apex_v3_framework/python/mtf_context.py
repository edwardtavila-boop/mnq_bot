"""
Multi-Timeframe Context Module
==============================
Loads higher-timeframe (1h) data and provides trend bias lookups at any timestamp.

Trend bias is computed via EMA20 vs EMA50 on the 1h chart:
  +1 : 1h uptrend (EMA20 > EMA50, price above both)
  -1 : 1h downtrend (EMA20 < EMA50, price below both)
   0 : 1h neutral / chop

The build_mtf_loader() function returns a callable that V3Backtester uses
during simulation: at each signal time, look up the prevailing 1h trend
and modify tier sizing accordingly.

Usage in V3:
  signal long + 1h uptrend = aligned (size boost 1.25x)
  signal long + 1h downtrend = counter (size penalty 0.75x)
  signal long + 1h neutral = no modifier
"""

import csv
from bisect import bisect_right


def _load_1h_csv(path):
    """Load 1h CSV and return list of (time, open, high, low, close, volume) sorted by time."""
    bars = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(
                {
                    "time": int(row["time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(float(row.get("volume", 0))),
                }
            )
    bars.sort(key=lambda b: b["time"])
    return bars


def _compute_emas(bars, ema_periods=(20, 50)):
    """Compute EMAs on closes. Mutates each bar with ema_<period> field."""
    if not bars:
        return
    multipliers = {p: 2.0 / (p + 1) for p in ema_periods}
    prev = dict.fromkeys(ema_periods)

    for bar in bars:
        close = bar["close"]
        for p in ema_periods:
            if prev[p] is None:
                bar[f"ema_{p}"] = close
                prev[p] = close
            else:
                ema = (close - prev[p]) * multipliers[p] + prev[p]
                bar[f"ema_{p}"] = ema
                prev[p] = ema


def _trend_at_bar(bar):
    """Return trend direction for a single 1h bar. -1, 0, +1."""
    e20 = bar.get("ema_20")
    e50 = bar.get("ema_50")
    close = bar["close"]
    if e20 is None or e50 is None:
        return 0
    # Uptrend: EMA20 > EMA50 AND price above EMA20
    if e20 > e50 and close > e20:
        return 1
    # Downtrend: EMA20 < EMA50 AND price below EMA20
    if e20 < e50 and close < e20:
        return -1
    return 0


def build_mtf_loader(csv_path, ema_periods=(20, 50)):
    """Load 1h data and return a function: ts -> trend_direction (-1, 0, +1).
    Uses the most recent 1h bar that has CLOSED before the given ts."""
    bars = _load_1h_csv(csv_path)
    _compute_emas(bars, ema_periods)
    times = [b["time"] for b in bars]
    bar_period_seconds = 3600  # 1 hour

    def loader(ts: int) -> int:
        # Find the most recent 1h bar that closed before ts
        # bar at time T represents the bar from T to T+3600
        # so it CLOSES at T+3600. Use bars where time + 3600 <= ts.
        adjusted_ts = ts - bar_period_seconds + 1  # last fully-closed bar
        idx = bisect_right(times, adjusted_ts) - 1
        if idx < 0:
            return 0
        return _trend_at_bar(bars[idx])

    return loader


def main():
    """Test the MTF loader on the 1h NQ file."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python mtf_context.py /tmp/historical/nq_1h.csv")
        return
    bars = _load_1h_csv(sys.argv[1])
    _compute_emas(bars)
    print(f"Loaded {len(bars):,} 1h bars")

    # Distribution of trend states across the dataset
    from collections import Counter

    counts = Counter(_trend_at_bar(b) for b in bars)
    print("\nTrend distribution across all 1h bars:")
    for direction, count in sorted(counts.items()):
        label = {1: "uptrend", -1: "downtrend", 0: "neutral"}.get(direction, "?")
        print(f"  {label:>10s}: {count:>5d} bars  ({count / len(bars) * 100:.1f}%)")

    # Spot check using the loader
    loader = build_mtf_loader(sys.argv[1])
    print("\nSpot checks:")
    sample_ts = bars[len(bars) // 2]["time"] + 1800  # 30 min into a bar
    print(f"  Mid-dataset trend: {loader(sample_ts)}")


if __name__ == "__main__":
    main()
