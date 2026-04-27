"""[REAL] Reference benchmark return series for attribution.

Three benchmarks: cash (zeros), MNQ intraday buy-hold, and naive momentum.
All return per-trade arrays aligned 1:1 with the strategy's trade ledger,
in USD per contract.

See the original [CONTRACT] docstring for the full API and nuances.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import polars as pl

# MNQ point-value: $2 per index point, and 1 tick = 0.25 points ⇒ $0.50/tick.
# Benchmarks return USD/contract. The trades DF carries entry/exit prices in
# index points; we multiply the price difference by POINT_VALUE_USD.
POINT_VALUE_USD = 2.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_columns(df: pl.DataFrame, *columns: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing} (have {df.columns})")


def _bar_at(bars: pl.DataFrame, ts: datetime) -> dict[str, Any] | None:
    """Return the bar whose start timestamp <= ts and whose end > ts.

    Assumes `bars` is sorted by `ts` ascending and uniformly spaced at
    bars_interval seconds. We approximate "contains ts" as the *last* bar
    with ts_bar <= ts.
    """
    # Binary search via polars is overkill; for per-trade use we need a
    # scan but datasets are small (typically <50k bars).
    # Convert once; we expect the caller to have sorted bars.
    tses = bars["ts"]
    # Fast path: exact match.
    idx = tses.search_sorted(ts, side="right") - 1
    if idx < 0 or idx >= len(bars):
        return None
    row = bars.row(int(idx), named=True)
    return row


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


def cash_returns(trades: pl.DataFrame) -> np.ndarray:
    return np.zeros(len(trades), dtype=np.float64)


def mnq_intraday_returns(trades: pl.DataFrame, bars: pl.DataFrame) -> np.ndarray:
    """Per-trade MNQ buy-and-hold over the trade's holding period.

    Enter at the open of the bar containing `entry_ts`, exit at the close
    of the bar containing `exit_ts`. Always long (this is the "market
    benchmark"); the strategy's short/long direction is NOT mirrored.

    Vectorized: a single polars `search_sorted` per timestamp column is
    ~100x faster than per-trade `_bar_at` on 10k trades.
    """

    _require_columns(trades, "entry_ts", "exit_ts")
    _require_columns(bars, "ts", "open", "close")

    n_trades = len(trades)
    n_bars = len(bars)
    if n_trades == 0 or n_bars == 0:
        return np.zeros(n_trades, dtype=np.float64)

    bars_ts = bars["ts"]
    bars_open = bars["open"].to_numpy()
    bars_close = bars["close"].to_numpy()

    entry_idx = bars_ts.search_sorted(trades["entry_ts"], side="right").to_numpy() - 1
    exit_idx = bars_ts.search_sorted(trades["exit_ts"], side="right").to_numpy() - 1

    valid = (entry_idx >= 0) & (entry_idx < n_bars) & (exit_idx >= 0) & (exit_idx < n_bars)

    out = np.zeros(n_trades, dtype=np.float64)
    if valid.any():
        ei = entry_idx[valid]
        xi = exit_idx[valid]
        out[valid] = (bars_close[xi] - bars_open[ei]) * POINT_VALUE_USD
    return out


def naive_momentum_returns(
    trades: pl.DataFrame,
    bars: pl.DataFrame,
    lookback_bars: int = 5,
) -> np.ndarray:
    """Naive momentum: at each trade's entry, if the prior `lookback_bars`
    closed net up, go long; net down, go short. Use the strategy's own
    stop and target distances so the risk shape matches.

    Exit: whichever of stop/target hits first within the holding window
    (entry_ts → exit_ts); otherwise time-stop at exit_bar close.
    """

    _require_columns(
        trades,
        "entry_ts",
        "exit_ts",
        "stop_dist_pts",
        "target_dist_pts",
    )
    _require_columns(bars, "ts", "open", "high", "low", "close")

    bars_ts = bars["ts"]
    bars_open = bars["open"].to_numpy()
    bars_high = bars["high"].to_numpy()
    bars_low = bars["low"].to_numpy()
    bars_close = bars["close"].to_numpy()

    # Vectorize both timestamp lookups.
    entry_idx_arr = bars_ts.search_sorted(trades["entry_ts"], side="right").to_numpy() - 1
    exit_idx_arr = bars_ts.search_sorted(trades["exit_ts"], side="right").to_numpy() - 1
    stop_dist_arr = trades["stop_dist_pts"].to_numpy()
    target_dist_arr = trades["target_dist_pts"].to_numpy()

    out = np.zeros(len(trades), dtype=np.float64)
    for i in range(len(trades)):
        entry_idx = int(entry_idx_arr[i])
        exit_idx = int(exit_idx_arr[i])
        if entry_idx < lookback_bars or exit_idx < entry_idx:
            out[i] = 0.0
            continue

        # Direction from lookback window [entry_idx - lookback, entry_idx).
        window_start = bars_close[entry_idx - lookback_bars]
        window_end = bars_close[entry_idx - 1]
        if window_end > window_start:
            direction = 1  # long
        elif window_end < window_start:
            direction = -1  # short
        else:
            out[i] = 0.0
            continue

        entry_px = float(bars_open[entry_idx])
        stop_dist = abs(float(stop_dist_arr[i]))
        target_dist = abs(float(target_dist_arr[i]))
        stop_px = entry_px - direction * stop_dist
        target_px = entry_px + direction * target_dist

        pnl_pts = 0.0
        resolved = False
        # Scan bars from entry_idx (inclusive) to exit_idx (inclusive).
        for j in range(entry_idx, exit_idx + 1):
            hi = float(bars_high[j])
            lo = float(bars_low[j])
            # Adverse-first: check stop, then target.
            if direction == 1:
                if lo <= stop_px:
                    pnl_pts = stop_px - entry_px
                    resolved = True
                    break
                if hi >= target_px:
                    pnl_pts = target_px - entry_px
                    resolved = True
                    break
            else:
                if hi >= stop_px:
                    pnl_pts = stop_px - entry_px
                    resolved = True
                    break
                if lo <= target_px:
                    pnl_pts = target_px - entry_px
                    resolved = True
                    break
        if not resolved:
            # Time-stop at exit bar close.
            pnl_pts = (float(bars_close[exit_idx]) - entry_px) * direction
            # Flip sign: direction=-1 wants lower close; pnl_pts already
            # = (close - entry) * -1 for short.
        out[i] = pnl_pts * POINT_VALUE_USD
    return out
