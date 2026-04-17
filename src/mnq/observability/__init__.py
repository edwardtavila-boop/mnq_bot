"""Observability stack for EVOLUTIONARY TRADING ALGO: structured logging, metrics, and tracing."""

from .logger import bind_trace_id, clear_trace_id, configure_logging, get_logger
from .metrics import (
    decision_latency_ms,
    fill_slippage_ticks,
    open_positions,
    orders_filled_total,
    orders_rejected_total,
    orders_submitted_total,
    pnl_dollars,
    reconcile_diffs_total,
    reset_metrics_for_tests,
    safety_decisions_total,
    start_metrics_server,
    ws_gap_total,
    ws_reconnects_total,
)
from .trace import TraceContext, current_trace_id, new_trace_id, trace

__all__ = [
    # Logger functions
    "configure_logging",
    "get_logger",
    "bind_trace_id",
    "clear_trace_id",
    # Metrics
    "orders_submitted_total",
    "orders_filled_total",
    "orders_rejected_total",
    "fill_slippage_ticks",
    "pnl_dollars",
    "open_positions",
    "ws_reconnects_total",
    "ws_gap_total",
    "safety_decisions_total",
    "decision_latency_ms",
    "reconcile_diffs_total",
    "start_metrics_server",
    "reset_metrics_for_tests",
    # Trace
    "new_trace_id",
    "trace",
    "TraceContext",
    "current_trace_id",
]
