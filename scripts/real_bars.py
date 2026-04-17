"""Load real MNQ 1-minute RTH bars from CSV into :class:`Bar` objects.

Data source: ``/sessions/kind-keen-faraday/mnt/Base/mnq_data/mnq_1m.csv``
with columns ``timestamp_utc, epoch_s, open, high, low, close, volume, session``.

The CSV already marks ``session`` as ``RTH`` / ``ETH``; we filter RTH bars and
group by exchange-local date. Each group becomes a "day" that matches the
390-bar structure the strategy expects.

Bars are built with UTC-aware timestamps; the strategy's window gate is
bar-index-based (0..389) not clock-based, so the timezone of ``ts`` does not
affect signal generation — it only affects journal timestamps.

``load_real_days`` returns ``list[list[Bar]]`` with the same shape
``pnl_report.synth_day`` produces one day at a time. Days are
chronologically ordered.
"""
from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc  # noqa: UP017
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import polars as pl  # noqa: E402

from mnq.core.types import Bar  # noqa: E402


def _pick(*candidates: str) -> Path:
    """Return the first existing path from candidates, else the first entry.

    Resolves the data-file location across the three hosts we run on:
    the Linux sandbox (/sessions/...), the Windows workstation (C:/mnq_data),
    and the OneDrive mirror (C:/Users/edwar/OneDrive/Desktop/Base/mnq_data).
    """
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p
    return Path(candidates[0])


DEFAULT_CSV = _pick(
    "C:/mnq_data/mnq_1m.csv",
    "C:/Users/edwar/OneDrive/Desktop/Base/mnq_data/mnq_1m.csv",
    "/sessions/kind-keen-faraday/mnt/Base/mnq_data/mnq_1m.csv",
)
CSV_5M = _pick(
    "C:/mnq_data/mnq_5m.csv",
    "C:/Users/edwar/OneDrive/Desktop/Base/mnq_data/mnq_5m.csv",
    "/sessions/kind-keen-faraday/mnt/Base/mnq_data/mnq_5m.csv",
)
# Multi-year Databento-format 1m tape (2019-05 → 2026-04, ~2.4M rows).
# Columns: time(epoch_s), open, high, low, close, volume — no session tag.
# Batch 3G uses this to extend the real-tape sample beyond the 15-day
# RTH-tagged CSV so the firm_vs_baseline CI can tighten.
CSV_DATABENTO_1M = _pick(
    "C:/mnq_data/databento/mnq1_1m.csv",
    "/sessions/kind-keen-faraday/mnt/mnq_bot/data/bars/databento/mnq1_1m.csv",
)


def _to_decimal(x: Any) -> Decimal:
    # Already stored as quarter-tick increments in the CSV, so str->Decimal is exact.
    return Decimal(str(x))


def load_real_days(
    csv_path: Path | str = DEFAULT_CSV,
    *,
    session: str = "RTH",
    min_bars_per_day: int = 380,
    timeframe_sec: int = 60,
) -> list[list[Bar]]:
    """Read a Polars-parseable CSV and emit one ``list[Bar]`` per trading day.

    Args:
        csv_path: Path to the CSV (must have the schema documented above).
        session: Filter to only rows with this ``session`` tag. Pass ``""`` to
            include everything (useful for ETH-inclusive backtests).
        min_bars_per_day: Drop partial days below this threshold. Default 380
            keeps a few short early-close days but drops fragments.

    Returns:
        List of days (list of Bar per day), chronologically sorted.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    df = pl.read_csv(path)
    # Optional session filter
    if session and "session" in df.columns:
        df = df.filter(pl.col("session") == session)

    # Derive exchange-local date from timestamp. For RTH we can use UTC
    # date directly since the RTH window 13:30-20:00 UTC never crosses
    # midnight. For ETH you'd need a proper tz conversion — out of scope here.
    df = df.with_columns(pl.col("timestamp_utc").str.slice(0, 10).alias("_date"))
    df = df.sort(["_date", "timestamp_utc"])

    days: list[list[Bar]] = []
    for (_date_str,), group in df.group_by(["_date"], maintain_order=True):
        if len(group) < min_bars_per_day:
            continue
        bars: list[Bar] = []
        for row in group.iter_rows(named=True):
            ts = datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00"))
            o = _to_decimal(row["open"])
            h = _to_decimal(row["high"])
            lo = _to_decimal(row["low"])
            c = _to_decimal(row["close"])
            # Fix OHLC invariants if the source has rounding/floating artifacts.
            oc_hi = max(o, c)
            oc_lo = min(o, c)
            if h < oc_hi:
                h = oc_hi
            if lo > oc_lo:
                lo = oc_lo
            vol = int(row.get("volume", 0) or 0)
            bars.append(
                Bar(
                    ts=ts,
                    open=o,
                    high=h,
                    low=lo,
                    close=c,
                    volume=vol,
                    timeframe_sec=timeframe_sec,
                )
            )
        days.append(bars)
    return days


def load_databento_days(
    csv_path: Path | str = CSV_DATABENTO_1M,
    *,
    rth_start_utc: tuple[int, int] = (13, 30),
    rth_end_utc: tuple[int, int] = (20, 0),
    min_bars_per_day: int = 380,
    timeframe_sec: int = 60,
    days_tail: int | None = None,
) -> list[list[Bar]]:
    """Load multi-year Databento 1m MNQ bars, RTH-filtered, grouped by UTC date.

    The Databento CSV has columns ``time, open, high, low, close, volume``
    where ``time`` is unix epoch seconds. Unlike the RTH-tagged CSV
    (``load_real_days``), there is no ``session`` column — we filter by
    UTC clock time to isolate RTH (13:30-20:00 UTC = 6h30m = 390 minutes).

    Args:
        csv_path: Databento-format CSV.
        rth_start_utc: (hour, minute) inclusive start of RTH window.
        rth_end_utc: (hour, minute) exclusive end of RTH window.
        min_bars_per_day: Drop partial days below this threshold (default 380
            keeps early-close holiday sessions, drops fragments).
        timeframe_sec: Emitted Bar.timeframe_sec (default 60).
        days_tail: If set, keep only the last N eligible days (most recent
            tape). Useful for Batch 3G windows like 30/60/90.

    Returns:
        List of days (list of Bar per day), chronologically sorted.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Databento CSV not found: {path}")

    df = pl.read_csv(path, columns=["time", "open", "high", "low", "close", "volume"])
    # Derive UTC date + HH:MM from epoch. Polars uses ms epoch for datetimes,
    # so cast epoch_s → epoch_ms → Datetime, then extract components.
    df = df.with_columns(
        (pl.col("time").cast(pl.Int64) * 1000).cast(pl.Datetime(time_unit="ms")).alias("_ts"),
    )
    df = df.with_columns(
        pl.col("_ts").dt.date().alias("_date"),
        # Cast HH/MM to Int32 — polars returns Int8 by default which silently
        # overflows when multiplied by 60 (23 * 60 > 127). Int32 is safe.
        pl.col("_ts").dt.hour().cast(pl.Int32).alias("_h"),
        pl.col("_ts").dt.minute().cast(pl.Int32).alias("_m"),
    )
    # RTH filter. 13:30 ≤ t < 20:00 UTC.
    sh, sm = rth_start_utc
    eh, em = rth_end_utc
    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em
    df = df.with_columns((pl.col("_h") * 60 + pl.col("_m")).alias("_mins"))
    df = df.filter((pl.col("_mins") >= start_mins) & (pl.col("_mins") < end_mins))
    df = df.sort(["_date", "time"])

    days: list[list[Bar]] = []
    for (_date_val,), group in df.group_by(["_date"], maintain_order=True):
        if len(group) < min_bars_per_day:
            continue
        bars: list[Bar] = []
        for row in group.iter_rows(named=True):
            ts = datetime.fromtimestamp(int(row["time"]), tz=UTC)
            o = _to_decimal(row["open"])
            h = _to_decimal(row["high"])
            lo = _to_decimal(row["low"])
            c = _to_decimal(row["close"])
            oc_hi = max(o, c)
            oc_lo = min(o, c)
            if h < oc_hi:
                h = oc_hi
            if lo > oc_lo:
                lo = oc_lo
            vol = int(row.get("volume", 0) or 0)
            bars.append(
                Bar(
                    ts=ts,
                    open=o,
                    high=h,
                    low=lo,
                    close=c,
                    volume=vol,
                    timeframe_sec=timeframe_sec,
                )
            )
        days.append(bars)

    if days_tail is not None and days_tail > 0:
        days = days[-days_tail:]
    return days


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_CSV)
    try:
        days = load_real_days(path)
    except FileNotFoundError as e:
        print(f"real_bars: {e}")
        sys.exit(1)
    print(f"real_bars: loaded {len(days)} days from {path}")
    if days:
        print(f"  first day: {days[0][0].ts.date()} ({len(days[0])} bars)")
        print(f"  last  day: {days[-1][0].ts.date()} ({len(days[-1])} bars)")
