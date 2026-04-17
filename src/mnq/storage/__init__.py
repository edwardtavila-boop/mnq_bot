"""[REAL] Durable event storage for live trading state.

The event journal provides crash-safe persistence of all trading events.
On restart, replay the journal to recover the trading state before the crash.
"""

from mnq.storage.journal import EventJournal, JournalEntry
from mnq.storage.schema import (
    FEATURE_STALENESS,
    ORDER_ACKED,
    ORDER_CANCELLED,
    ORDER_FILLED,
    ORDER_PARTIAL,
    ORDER_REJECTED,
    ORDER_SUBMITTED,
    ORDER_WORKING,
    PNL_UPDATE,
    POSITION_UPDATE,
    RECONCILE_DIFF,
    RECONCILE_START,
    SAFETY_DECISION,
    WS_CONNECT,
    WS_DISCONNECT,
    WS_GAP_DETECTED,
)

__all__ = [
    "EventJournal",
    "JournalEntry",
    "FEATURE_STALENESS",
    "ORDER_ACKED",
    "ORDER_CANCELLED",
    "ORDER_FILLED",
    "ORDER_PARTIAL",
    "ORDER_REJECTED",
    "ORDER_SUBMITTED",
    "ORDER_WORKING",
    "PNL_UPDATE",
    "POSITION_UPDATE",
    "RECONCILE_DIFF",
    "RECONCILE_START",
    "SAFETY_DECISION",
    "WS_CONNECT",
    "WS_DISCONNECT",
    "WS_GAP_DETECTED",
]
