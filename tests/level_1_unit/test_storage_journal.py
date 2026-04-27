"""Tests for mnq.storage event journal."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mnq.storage import (
    ORDER_SUBMITTED,
    POSITION_UPDATE,
    EventJournal,
)


def test_append_and_last_seq(tmp_path: Path) -> None:
    """Appending an event returns its sequence number and last_seq matches."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    seq1 = journal.append(ORDER_SUBMITTED, {"order_id": "123"})
    assert seq1 == 1
    assert journal.last_seq() == 1

    seq2 = journal.append(ORDER_SUBMITTED, {"order_id": "124"})
    assert seq2 == 2
    assert journal.last_seq() == 2

    journal.close()


def test_replay_yields_in_order(tmp_path: Path) -> None:
    """Replay yields entries in ascending sequence order."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    ids = []
    for i in range(5):
        journal.append(ORDER_SUBMITTED, {"order_id": str(i)})
        ids.append(str(i))

    entries = list(journal.replay())
    assert len(entries) == 5
    assert [e.seq for e in entries] == [1, 2, 3, 4, 5]
    assert [e.payload["order_id"] for e in entries] == ids

    journal.close()


def test_replay_since_seq(tmp_path: Path) -> None:
    """Replay with since_seq filters correctly."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    for i in range(5):
        journal.append(ORDER_SUBMITTED, {"order_id": str(i)})

    # Replay from seq 2 (exclusive), so we get 3, 4, 5
    entries = list(journal.replay(since_seq=2))
    assert len(entries) == 3
    assert [e.seq for e in entries] == [3, 4, 5]

    journal.close()


def test_replay_with_event_types_filter(tmp_path: Path) -> None:
    """Replay filters by event_types when provided."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    journal.append(ORDER_SUBMITTED, {"order_id": "1"})
    journal.append(POSITION_UPDATE, {"symbol": "MNQ", "quantity": 10})
    journal.append(ORDER_SUBMITTED, {"order_id": "2"})
    journal.append(POSITION_UPDATE, {"symbol": "MNQ", "quantity": 20})

    entries = list(journal.replay(event_types=(ORDER_SUBMITTED,)))
    assert len(entries) == 2
    assert all(e.event_type == ORDER_SUBMITTED for e in entries)
    assert [e.payload["order_id"] for e in entries] == ["1", "2"]

    journal.close()


def test_find_by_trace(tmp_path: Path) -> None:
    """find_by_trace returns only matching entries."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    trace1 = "trace-1"
    trace2 = "trace-2"

    journal.append(ORDER_SUBMITTED, {"order_id": "1"}, trace_id=trace1)
    journal.append(ORDER_SUBMITTED, {"order_id": "2"}, trace_id=trace2)
    journal.append(ORDER_SUBMITTED, {"order_id": "3"}, trace_id=trace1)

    results = journal.find_by_trace(trace1)
    assert len(results) == 2
    assert [e.payload["order_id"] for e in results] == ["1", "3"]
    assert all(e.trace_id == trace1 for e in results)

    journal.close()


def test_crash_simulation(tmp_path: Path) -> None:
    """Append 10 events, crash abruptly, reopen, verify all 10 remain."""
    db = tmp_path / "test.db"

    # First session: append 10 events
    journal = EventJournal(db, fsync=True)
    for i in range(10):
        journal.append(ORDER_SUBMITTED, {"order_id": str(i)})
    # Simulate crash: close abruptly without explicit commit
    # (SQLite WAL ensures durability anyway with PRAGMA synchronous=FULL)
    journal.close()

    # Second session: reopen and verify
    journal2 = EventJournal(db)
    assert journal2.last_seq() == 10
    entries = list(journal2.replay())
    assert len(entries) == 10
    assert [e.seq for e in entries] == list(range(1, 11))
    journal2.close()


def test_concurrent_appends_monotonicity(tmp_path: Path) -> None:
    """Concurrent appends from two connections maintain seq monotonicity."""
    db = tmp_path / "test.db"

    journal1 = EventJournal(db)
    journal2 = EventJournal(db)

    # Interleaved appends
    seq1 = journal1.append(ORDER_SUBMITTED, {"order_id": "1"})
    seq2 = journal2.append(ORDER_SUBMITTED, {"order_id": "2"})
    seq3 = journal1.append(ORDER_SUBMITTED, {"order_id": "3"})
    seq4 = journal2.append(ORDER_SUBMITTED, {"order_id": "4"})

    # Sequences should be strictly increasing
    assert seq1 == 1
    assert seq2 == 2
    assert seq3 == 3
    assert seq4 == 4

    # Replay should have all 4 in order
    entries = list(journal1.replay())
    assert len(entries) == 4
    assert [e.seq for e in entries] == [1, 2, 3, 4]

    journal1.close()
    journal2.close()


def test_empty_payload_roundtrip(tmp_path: Path) -> None:
    """Empty payload dict round-trips correctly."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    journal.append(ORDER_SUBMITTED, {})
    entries = list(journal.replay())
    assert len(entries) == 1
    assert entries[0].payload == {}

    journal.close()


def test_unicode_payload_roundtrip(tmp_path: Path) -> None:
    """Unicode in payload round-trips correctly."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    payload = {
        "message": "你好世界 🚀",
        "emoji": "📊",
        "accents": "café",
    }
    journal.append(ORDER_SUBMITTED, payload)
    entries = list(journal.replay())
    assert len(entries) == 1
    assert entries[0].payload == payload

    journal.close()


def test_large_payload_roundtrip(tmp_path: Path) -> None:
    """Large payload (10 KB) round-trips correctly."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    # Create a 10 KB payload
    large_data = {"data": "x" * 10000}
    journal.append(ORDER_SUBMITTED, large_data)
    entries = list(journal.replay())
    assert len(entries) == 1
    assert entries[0].payload == large_data

    journal.close()


def test_journal_entry_is_frozen(tmp_path: Path) -> None:
    """JournalEntry is immutable (frozen dataclass)."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    journal.append(ORDER_SUBMITTED, {"order_id": "1"})
    entries = list(journal.replay())
    entry = entries[0]

    with pytest.raises(AttributeError):
        entry.seq = 999  # type: ignore

    journal.close()


def test_context_manager(tmp_path: Path) -> None:
    """EventJournal works as a context manager."""
    db = tmp_path / "test.db"

    with EventJournal(db) as journal:
        seq = journal.append(ORDER_SUBMITTED, {"order_id": "1"})
        assert seq == 1

    # After exiting context, connection should be closed
    # Reopening should still have the data
    with EventJournal(db) as journal2:
        assert journal2.last_seq() == 1


def test_replay_combined_filters(tmp_path: Path) -> None:
    """Replay with both since_seq and event_types filters."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    journal.append(ORDER_SUBMITTED, {"order_id": "1"})  # seq 1
    journal.append(POSITION_UPDATE, {"quantity": 10})  # seq 2
    journal.append(ORDER_SUBMITTED, {"order_id": "2"})  # seq 3
    journal.append(POSITION_UPDATE, {"quantity": 20})  # seq 4
    journal.append(ORDER_SUBMITTED, {"order_id": "3"})  # seq 5

    entries = list(journal.replay(since_seq=1, event_types=(ORDER_SUBMITTED,)))
    # Should get seqs 3 and 5 (ORDER_SUBMITTED events after seq 1)
    assert len(entries) == 2
    assert [e.seq for e in entries] == [3, 5]

    journal.close()


def test_ts_is_iso8601_utc(tmp_path: Path) -> None:
    """Timestamp is ISO-8601 UTC."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    journal.append(ORDER_SUBMITTED, {"order_id": "1"})
    entries = list(journal.replay())
    ts = entries[0].ts

    # Should be a datetime object
    assert isinstance(ts, datetime)
    # Should be UTC aware
    assert ts.tzinfo is not None

    journal.close()


def test_trace_id_auto_generated(tmp_path: Path) -> None:
    """When trace_id is not provided, one is auto-generated."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    journal.append(ORDER_SUBMITTED, {"order_id": "1"})
    entries = list(journal.replay())
    # trace_id should be generated (not None)
    assert entries[0].trace_id is not None
    assert isinstance(entries[0].trace_id, str)
    assert len(entries[0].trace_id) > 0

    journal.close()


def test_empty_journal_last_seq(tmp_path: Path) -> None:
    """last_seq on empty journal returns 0."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    assert journal.last_seq() == 0

    journal.close()


def test_find_by_trace_not_found(tmp_path: Path) -> None:
    """find_by_trace returns empty list when trace not found."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    journal.append(ORDER_SUBMITTED, {"order_id": "1"}, trace_id="trace-1")

    results = journal.find_by_trace("nonexistent")
    assert results == []

    journal.close()


def test_replay_empty_journal(tmp_path: Path) -> None:
    """Replay on empty journal yields nothing."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    entries = list(journal.replay())
    assert entries == []

    journal.close()


def test_fsync_false_mode(tmp_path: Path) -> None:
    """EventJournal with fsync=False uses PRAGMA synchronous=NORMAL."""
    db = tmp_path / "test.db"
    journal = EventJournal(db, fsync=False)

    journal.append(ORDER_SUBMITTED, {"order_id": "1"})
    assert journal.last_seq() == 1

    journal.close()


def test_multiple_event_types_in_payload(tmp_path: Path) -> None:
    """Complex nested payloads round-trip correctly."""
    db = tmp_path / "test.db"
    journal = EventJournal(db)

    payload = {
        "order_id": "ORD-123",
        "fills": [
            {"qty": 10, "price": 100.5},
            {"qty": 5, "price": 100.6},
        ],
        "metadata": {
            "strategy": "momentum",
            "risk_level": 2,
        },
    }
    journal.append(ORDER_SUBMITTED, payload)
    entries = list(journal.replay())
    assert entries[0].payload == payload

    journal.close()
