"""Structured JSON logging via structlog."""

import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.processors import TimeStamper


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = True,
    extra_processors: list[Any] | None = None,
) -> None:
    """Configure structlog globally. Call once at process start.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: If True, output JSON; if False, human-readable format.
        extra_processors: Additional structlog processors to include.
    """
    if extra_processors is None:
        extra_processors = []

    # Common processors for all configurations
    common_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        processors = (
            common_processors
            + extra_processors
            + [
                structlog.processors.JSONRenderer(),
            ]
        )
    else:
        processors = (
            common_processors
            + extra_processors
            + [
                structlog.dev.ConsoleRenderer(),
            ]
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(min_level=level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Return a structlog BoundLogger with `logger=name` bound.

    Args:
        name: Logger name (typically __name__ of calling module).

    Returns:
        A BoundLogger with logger name pre-bound.
    """
    logger = structlog.get_logger()
    return logger.bind(logger=name)


def bind_trace_id(trace_id: str) -> None:
    """Bind trace_id to contextvars so it's included on all subsequent logs.

    This uses structlog's contextvars integration to avoid cross-contamination
    in threaded or async contexts.

    Args:
        trace_id: Trace ID string to bind.
    """
    bind_contextvars(trace_id=trace_id)


def clear_trace_id() -> None:
    """Remove trace_id from context variables."""
    clear_contextvars()
