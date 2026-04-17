"""Trace ID management for distributed tracing."""

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .logger import bind_trace_id, clear_trace_id

_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)


def new_trace_id() -> str:
    """Generate a new trace ID.

    Returns:
        A 16-character hex string (UUID4 truncated).
    """
    return uuid4().hex[:16]


@contextmanager
def trace(trace_id: str | None = None) -> Iterator[str]:
    """Context manager for binding trace ID to logs and context.

    Automatically generates a new trace ID if none is provided.
    Binds the trace ID to structlog context and yields it.
    Automatically unbinds on exit.

    Args:
        trace_id: Optional trace ID. If None, generates a new one.

    Yields:
        The trace ID in use.

    Example:
        with trace() as tid:
            logger.info("processing", trace=tid)
    """
    if trace_id is None:
        trace_id = new_trace_id()

    # Bind to structlog
    bind_trace_id(trace_id)

    # Set in contextvars
    token = _current_trace_id.set(trace_id)

    try:
        yield trace_id
    finally:
        # Reset contextvars
        _current_trace_id.reset(token)
        # Clear structlog context
        clear_trace_id()


@dataclass(frozen=True)
class TraceContext:
    """Information about the current trace.

    Attributes:
        trace_id: Unique identifier for this trace.
        parent_id: ID of parent trace, if any.
        started_at: Timestamp when trace began.
    """

    trace_id: str
    parent_id: str | None = None
    started_at: datetime = datetime.now(UTC)


def current_trace_id() -> str | None:
    """Get the current trace ID from context.

    Returns:
        The current trace ID, or None if no trace is active.
    """
    return _current_trace_id.get()
