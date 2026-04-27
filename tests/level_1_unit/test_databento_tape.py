"""Tests for ``mnq.tape.databento_tape`` -- the streaming CSV reader.

Pin the contract B4 (per-bar Firm review) depends on:

  * Each CSV row -> exactly one Bar
  * Bars are emitted in chronological order
  * RTH filter is on by default and drops overnight bars
  * Iterator + load wrappers agree on outputs
  * skip_first / max_bars work as documented

Uses a tiny synthetic CSV so the test is fast and deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnq.tape.databento_tape import (
    DEFAULT_DATABENTO_5M,
    iter_databento_bars,
    load_databento_bars,
)


@pytest.fixture
def tiny_csv(tmp_path: Path) -> Path:
    """Hand-written tape with a known mix of RTH + overnight bars."""
    csv = tmp_path / "tiny_mnq_5m.csv"
    # Epoch seconds (UTC):
    #   2024-01-02 13:30:00 UTC = 1704202200 (RTH start)
    #   2024-01-02 13:35:00 UTC = 1704202500 (RTH)
    #   2024-01-02 13:40:00 UTC = 1704202800 (RTH)
    #   2024-01-02 20:00:00 UTC = 1704225600 (RTH end-exclusive)
    #   2024-01-02 21:00:00 UTC = 1704229200 (overnight)
    #   2024-01-02 03:00:00 UTC = 1704164400 (pre-RTH)
    csv.write_text(
        "time,open,high,low,close,volume\n"
        # Pre-RTH (should be filtered when rth_only=True)
        "1704164400,16800.0,16802.0,16798.0,16801.0,100\n"
        # RTH bars
        "1704202200,16810.0,16815.0,16808.0,16812.0,500\n"
        "1704202500,16812.0,16820.0,16811.0,16819.0,800\n"
        "1704202800,16819.0,16825.0,16817.0,16823.0,750\n"
        # Exactly at end-exclusive boundary (should be filtered)
        "1704225600,16830.0,16832.0,16828.0,16830.0,200\n"
        # Overnight (should be filtered when rth_only=True)
        "1704229200,16830.0,16835.0,16828.0,16832.0,100\n",
        encoding="utf-8",
    )
    return csv


def test_iter_yields_bars_in_order(tiny_csv: Path) -> None:
    bars = list(iter_databento_bars(tiny_csv, rth_only=True))
    assert len(bars) == 3, f"expected 3 RTH bars, got {len(bars)}"
    times = [b.ts for b in bars]
    assert times == sorted(times), "bars must be chronologically ordered"
    assert times[0] == datetime(2024, 1, 2, 13, 30, tzinfo=UTC)
    assert times[-1] == datetime(2024, 1, 2, 13, 40, tzinfo=UTC)


def test_rth_filter_drops_overnight_and_boundary(tiny_csv: Path) -> None:
    """13:30 inclusive, 20:00 exclusive: pre-RTH and the 20:00 bar both drop."""
    bars = list(iter_databento_bars(tiny_csv, rth_only=True))
    for b in bars:
        mins = b.ts.hour * 60 + b.ts.minute
        assert 810 <= mins < 1200, f"bar at {b.ts} outside RTH window"


def test_rth_off_yields_everything(tiny_csv: Path) -> None:
    bars = list(iter_databento_bars(tiny_csv, rth_only=False))
    assert len(bars) == 6, "rth_only=False should keep all rows"


def test_load_wrapper_matches_iter(tiny_csv: Path) -> None:
    iterated = list(iter_databento_bars(tiny_csv))
    loaded = load_databento_bars(tiny_csv)
    assert len(iterated) == len(loaded)
    for a, b in zip(iterated, loaded, strict=False):
        assert a.ts == b.ts
        assert a.open == b.open
        assert a.close == b.close


def test_max_bars_caps_output(tiny_csv: Path) -> None:
    bars = list(iter_databento_bars(tiny_csv, max_bars=2))
    assert len(bars) == 2


def test_skip_first_skips_initial_bars(tiny_csv: Path) -> None:
    full = list(iter_databento_bars(tiny_csv))
    skipped = list(iter_databento_bars(tiny_csv, skip_first=1))
    assert len(skipped) == len(full) - 1
    assert skipped[0].ts == full[1].ts


def test_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(iter_databento_bars(tmp_path / "does_not_exist.csv"))


def test_default_tape_path_points_at_databento_dir() -> None:
    """The DEFAULT_DATABENTO_5M constant must resolve under the canonical
    BARS_DATABENTO_DIR (mnq.core.paths). This pin catches drift if the
    paths registry is renamed and the tape forgets to follow."""
    from mnq.core.paths import BARS_DATABENTO_DIR

    assert DEFAULT_DATABENTO_5M.parent == BARS_DATABENTO_DIR
    assert DEFAULT_DATABENTO_5M.name == "mnq1_5m.csv"


def test_volume_coerced_to_int(tiny_csv: Path) -> None:
    """The CSV stores volume as float (e.g. '500'); Bar.volume is int."""
    bars = list(iter_databento_bars(tiny_csv))
    for b in bars:
        assert isinstance(b.volume, int)


def test_bar_timeframe_sec_matches_arg(tiny_csv: Path) -> None:
    bars = list(iter_databento_bars(tiny_csv, timeframe_sec=300))
    for b in bars:
        assert b.timeframe_sec == 300

    bars = list(iter_databento_bars(tiny_csv, timeframe_sec=60))
    for b in bars:
        assert b.timeframe_sec == 60
