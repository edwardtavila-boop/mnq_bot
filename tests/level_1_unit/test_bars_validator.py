"""Tests for mnq.core.bars_validator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mnq.core.bars_validator import (
    BarSequenceError,
    dedupe_sorted,
    validate_bar_sequence,
)

from ._bars import constant_bars, make_bar


def test_empty_sequence_is_ok():
    report = validate_bar_sequence([])
    assert report.ok
    assert report.n_bars == 0


def test_clean_sequence_passes():
    bars = constant_bars(10)
    report = validate_bar_sequence(bars)
    assert report.ok
    assert report.n_bars == 10
    assert report.duplicates == report.backwards == report.gaps == 0


def test_duplicate_timestamp_raises_in_strict_mode():
    bars = constant_bars(3)
    dup = make_bar(bars[1].ts, 100, 100, 100, 100, 100)
    bad = [bars[0], bars[1], dup, bars[2]]
    with pytest.raises(BarSequenceError, match="duplicate"):
        validate_bar_sequence(bad)


def test_duplicate_timestamp_reports_in_non_strict_mode():
    bars = constant_bars(3)
    dup = make_bar(bars[1].ts, 100, 100, 100, 100, 100)
    bad = [bars[0], bars[1], dup, bars[2]]
    report = validate_bar_sequence(bad, strict=False)
    assert not report.ok
    assert report.duplicates == 1
    assert len(report.anomalies) == 1


def test_backwards_timestamp_detected():
    bars = constant_bars(3)
    back = make_bar(bars[0].ts - timedelta(seconds=60), 100, 100, 100, 100)
    bad = [bars[0], bars[1], back]
    with pytest.raises(BarSequenceError, match="is before"):
        validate_bar_sequence(bad)


def test_mid_gap_allowed_when_under_max_multiple():
    # 5-minute gap inside a 1-min timeframe is well within the 60x default.
    start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    b0 = make_bar(start, 100, 100, 100, 100)
    b1 = make_bar(start + timedelta(minutes=5), 100, 100, 100, 100)
    report = validate_bar_sequence([b0, b1])
    assert report.ok
    assert report.gaps == 0


def test_oversized_gap_raises():
    start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    # 120-minute gap > 60x * 60s
    b0 = make_bar(start, 100, 100, 100, 100)
    b1 = make_bar(start + timedelta(minutes=120), 100, 100, 100, 100)
    with pytest.raises(BarSequenceError, match="oversized gap"):
        validate_bar_sequence([b0, b1])


def test_allow_gaps_false_rejects_any_non_adjacent():
    bars = constant_bars(3)
    out = [bars[0], bars[2]]  # skipping bars[1]
    with pytest.raises(BarSequenceError, match="unexpected gap"):
        validate_bar_sequence(out, allow_gaps=False)


def test_timeframe_mismatch_detected():
    start = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    b0 = make_bar(start, 100, 100, 100, 100, tf_sec=60)
    b1 = make_bar(start + timedelta(seconds=60), 100, 100, 100, 100, tf_sec=300)
    with pytest.raises(BarSequenceError, match="timeframe_sec"):
        validate_bar_sequence([b0, b1])


def test_dedupe_sorted_removes_exact_duplicates():
    bars = constant_bars(3)
    dup = make_bar(bars[1].ts, 100, 100, 100, 100, 999)
    dirty = [bars[0], bars[1], dup, bars[2]]
    clean = dedupe_sorted(dirty)
    assert len(clean) == 3
    # Last-writer-wins: the duplicate with volume=999 should be kept.
    assert clean[1].volume == 999


def test_dedupe_sorted_preserves_non_duplicate_sequence():
    bars = constant_bars(5)
    assert dedupe_sorted(bars) == list(bars)


def test_dedupe_sorted_empty_input():
    assert dedupe_sorted([]) == []
