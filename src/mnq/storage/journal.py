"""[REAL] Durable append-only event journal using SQLite.

SQLite is preferred over JSONL for:
  - Atomic writes: PRAGMA synchronous=FULL ensures no partial writes on crash
  - Efficient indexing: Queries by trace_id or event_type are O(log n)
  - WAL mode: Write-ahead logging allows readers while writers append
  - ACID guarantees: Corruption-free recovery without replay validation

The journal maintains a monotonic sequence number for each event. On crash,
the database is reopened and all committed events are replayed from disk.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class JournalEntry:
    """A single immutable entry in the event journal."""

    seq: int
    ts: datetime
    event_type: str
    trace_id: str | None
    payload: dict[str, Any]


class EventJournal:
    """Append-only durable event journal backed by SQLite.

    Thread-safe within a single process (SQLite serialization).
    Multiple processes with WAL mode can write concurrently but sequence
    numbers remain monotonic due to AUTOINCREMENT.
    """

    def __init__(self, path: Path, *, fsync: bool = True) -> None:
        """Initialize the journal at the given path.

        Args:
            path: Path to the SQLite database file.
            fsync: If True, PRAGMA synchronous=FULL ensures durability
                   via fsync. If False, uses NORMAL (faster, less durable).
        """
        self.path = Path(path)
        self.fsync = fsync
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Create the database and schema if needed."""
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            # Enable WAL mode for concurrent read-write
            conn.execute("PRAGMA journal_mode=WAL")

            # Set synchronous mode based on fsync preference
            sync_level = "FULL" if self.fsync else "NORMAL"
            conn.execute(f"PRAGMA synchronous={sync_level}")

            # Create table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    trace_id TEXT,
                    payload TEXT NOT NULL
                )
            """)

            # Create indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace ON events(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON events(event_type)")

            conn.commit()
            self._conn = conn
        except Exception:
            conn.close()
            raise

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.path))
            self._conn.row_factory = sqlite3.Row
            sync_level = "FULL" if self.fsync else "NORMAL"
            self._conn.execute(f"PRAGMA synchronous={sync_level}")
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> int:
        """Append an event to the journal and return its sequence number.

        Args:
            event_type: The event type constant (e.g., "order.submitted").
            payload: The event payload as a JSON-serializable dict.
            trace_id: Optional trace ID for correlation across events.

        Returns:
            The monotonically increasing sequence number of the appended event.

        Raises:
            sqlite3.Error: If the write fails (e.g., disk full).
        """
        if trace_id is None:
            trace_id = str(uuid4())

        ts = datetime.now(UTC).isoformat()
        payload_json = json.dumps(payload)

        conn = self._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO events (ts, event_type, trace_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (ts, event_type, trace_id, payload_json),
        )
        seq: int = cursor.lastrowid  # type: ignore[assignment]
        conn.commit()

        return seq

    def replay(
        self,
        *,
        since_seq: int = 0,
        event_types: tuple[str, ...] | None = None,
    ) -> Iterator[JournalEntry]:
        """Iterate over journal entries in sequence order.

        Args:
            since_seq: Start from this sequence number (inclusive).
                       0 means start from the beginning.
            event_types: If provided, yield only events of these types.

        Yields:
            JournalEntry objects in ascending sequence order.
        """
        conn = self._get_conn()

        if event_types:
            placeholders = ",".join("?" * len(event_types))
            query = f"""
                SELECT seq, ts, event_type, trace_id, payload
                FROM events
                WHERE seq > ? AND event_type IN ({placeholders})
                ORDER BY seq ASC
            """
            params: tuple[Any, ...] = (since_seq,) + event_types
        else:
            query = """
                SELECT seq, ts, event_type, trace_id, payload
                FROM events
                WHERE seq > ?
                ORDER BY seq ASC
            """
            params = (since_seq,)

        cursor = conn.execute(query, params)
        for row in cursor:
            yield JournalEntry(
                seq=row["seq"],
                ts=datetime.fromisoformat(row["ts"]),
                event_type=row["event_type"],
                trace_id=row["trace_id"],
                payload=json.loads(row["payload"]),
            )

    def last_seq(self) -> int:
        """Return the sequence number of the most recent event, or 0 if empty."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT MAX(seq) as max_seq FROM events")
        row = cursor.fetchone()
        return row["max_seq"] if row and row["max_seq"] is not None else 0

    def find_by_trace(self, trace_id: str) -> list[JournalEntry]:
        """Find all events with a given trace ID.

        Args:
            trace_id: The trace ID to search for.

        Returns:
            List of JournalEntry objects in sequence order.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """
            SELECT seq, ts, event_type, trace_id, payload
            FROM events
            WHERE trace_id = ?
            ORDER BY seq ASC
            """,
            (trace_id,),
        )
        return [
            JournalEntry(
                seq=row["seq"],
                ts=datetime.fromisoformat(row["ts"]),
                event_type=row["event_type"],
                trace_id=row["trace_id"],
                payload=json.loads(row["payload"]),
            )
            for row in cursor
        ]

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> EventJournal:
        """Context manager entry."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit."""
        self.close()
