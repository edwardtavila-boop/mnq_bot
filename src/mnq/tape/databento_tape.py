"""[REAL] Streaming reader for the Databento-format MNQ CSV tape.

Schema (matches ``data/bars/databento/mnq1_{1,5}m.csv``):

    time,open,high,low,close,volume
    1557093600,7748.75,7748.75,7748.75,7748.75,1.0
    ...

``time`` is unix epoch seconds (UTC); OHLCV are 64-bit floats. Each row
becomes one :class:`mnq.core.types.Bar`. Volume is rounded to int.

Why this exists
---------------
B4 closure (Red Team review 2026-04-25). The live runtime
(``scripts/run_eta_live.py``) needs a real-tape source to feed the
per-bar Firm review. Prior to B4, ``firm_live_review.py`` used a
hardcoded synthetic bar (``firm_engine.Bar(time=0, open=21000, ...)``)
which made the resulting verdict structurally meaningless — it scored
the same fictional bar every run.

This module is the canonical real-tape adapter. It is intentionally
small and dependency-free (stdlib ``csv`` + ``decimal``) so the live
runtime can import it without dragging in polars or pandas.

Two entry points
----------------
* :func:`iter_databento_bars` — generator, lazy, suitable for live-like
  per-tick consumption. Each call yields one ``Bar`` and reads the
  next CSV row on demand. This is what ``ApexRuntime._tick()`` uses.
* :func:`load_databento_bars` — eager, returns ``list[Bar]``. Useful
  for tests that need to assert exact lengths / replay determinism.

Both honor an optional ``rth_only`` filter (default True) restricting
to the 13:30-20:00 UTC RTH window so the runtime only reviews
liquid hours.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from mnq.core.paths import BARS_DATABENTO_DIR
from mnq.core.types import Bar

# Default tape: 5-minute MNQ bars, the primary timeframe for the Apex V3
# strategy variants. The 1m tape is also available at mnq1_1m.csv but the
# 5m cadence matches what the strategy actually consumes.
DEFAULT_DATABENTO_5M: Path = BARS_DATABENTO_DIR / "mnq1_5m.csv"
DEFAULT_DATABENTO_1M: Path = BARS_DATABENTO_DIR / "mnq1_1m.csv"

# RTH window in UTC: 13:30-20:00 (= 9:30 ET to 16:00 ET, ignoring DST).
# Conservative for live: skips the open/close gap noise and overnight chop.
RTH_START_MIN_UTC: int = 13 * 60 + 30  # 810
RTH_END_MIN_UTC: int = 20 * 60  # 1200


@dataclass(frozen=True)
class TapeStats:
    """Summary of a tape iteration. Cheap to assemble; used by callers
    that want to emit a banner + journal a session-start record."""

    rows_read: int
    bars_emitted: int
    rows_filtered: int
    first_ts: datetime | None
    last_ts: datetime | None


def _is_rth(ts: datetime) -> bool:
    mins = ts.hour * 60 + ts.minute
    return RTH_START_MIN_UTC <= mins < RTH_END_MIN_UTC


def _row_to_bar(row: dict[str, str], *, timeframe_sec: int) -> Bar:
    epoch = int(float(row["time"]))
    ts = datetime.fromtimestamp(epoch, tz=UTC)
    o = Decimal(str(row["open"]))
    h = Decimal(str(row["high"]))
    lo = Decimal(str(row["low"]))
    c = Decimal(str(row["close"]))
    # Defensive: tape rows occasionally have rounding artifacts where
    # close is fractionally above high or below low. Snap them in so
    # Bar.__post_init__ doesn't reject the row.
    oc_hi = max(o, c)
    oc_lo = min(o, c)
    if h < oc_hi:
        h = oc_hi
    if lo > oc_lo:
        lo = oc_lo
    vol = int(float(row.get("volume", 0) or 0))
    return Bar(
        ts=ts,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=vol,
        timeframe_sec=timeframe_sec,
    )


def iter_databento_bars(
    csv_path: Path | str = DEFAULT_DATABENTO_5M,
    *,
    rth_only: bool = True,
    timeframe_sec: int = 300,
    skip_first: int = 0,
    max_bars: int | None = None,
) -> Iterator[Bar]:
    """Stream :class:`Bar` objects from a Databento-format CSV.

    Args:
        csv_path: Path to the CSV. Default is the canonical 5m tape.
        rth_only: If True (default), drop bars outside 13:30-20:00 UTC.
        timeframe_sec: Bar duration in seconds. 300 for the 5m tape,
            60 for the 1m tape.
        skip_first: Skip the first N rows after RTH filtering. Useful
            for resuming from a checkpoint without re-reviewing.
        max_bars: If set, stop after emitting this many bars.

    Yields:
        ``Bar`` objects in chronological (ascending-time) order.

    Raises:
        FileNotFoundError: if ``csv_path`` does not exist.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"databento tape not found: {path}")
    emitted = 0
    skipped = 0
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                bar = _row_to_bar(row, timeframe_sec=timeframe_sec)
            except (ValueError, KeyError):
                # Skip malformed rows rather than crash the runtime.
                continue
            if rth_only and not _is_rth(bar.ts):
                continue
            if skipped < skip_first:
                skipped += 1
                continue
            yield bar
            emitted += 1
            if max_bars is not None and emitted >= max_bars:
                return


def load_databento_bars(
    csv_path: Path | str = DEFAULT_DATABENTO_5M,
    *,
    rth_only: bool = True,
    timeframe_sec: int = 300,
    skip_first: int = 0,
    max_bars: int | None = None,
) -> list[Bar]:
    """Eager wrapper around :func:`iter_databento_bars`.

    Returns a fully-materialized ``list[Bar]``. Use this in tests where
    you need to assert exact lengths or replay determinism; use the
    iterator in the runtime where memory + latency matter.
    """
    return list(
        iter_databento_bars(
            csv_path,
            rth_only=rth_only,
            timeframe_sec=timeframe_sec,
            skip_first=skip_first,
            max_bars=max_bars,
        ),
    )
