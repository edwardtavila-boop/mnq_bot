"""Prometheus metrics wrappers for trading system observability."""

from contextlib import suppress

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, start_http_server

# Pre-registered metrics with consistent labeling
orders_submitted_total = Counter(
    "orders_submitted_total",
    "Total orders submitted",
    labelnames=["side", "order_type"],
)

orders_filled_total = Counter(
    "orders_filled_total",
    "Total orders filled",
    labelnames=["side", "exit_reason"],
)

orders_rejected_total = Counter(
    "orders_rejected_total",
    "Total orders rejected",
    labelnames=["reason"],
)

fill_slippage_ticks = Histogram(
    "fill_slippage_ticks",
    "Slippage in ticks at fill",
    labelnames=["side"],
)

pnl_dollars = Gauge(
    "pnl_dollars",
    "Session P&L in dollars",
)

open_positions = Gauge(
    "open_positions",
    "Net open position quantity",
)

ws_reconnects_total = Counter(
    "ws_reconnects_total",
    "Total WebSocket reconnections",
)

ws_gap_total = Counter(
    "ws_gap_total",
    "Total WebSocket message gaps detected",
)

safety_decisions_total = Counter(
    "safety_decisions_total",
    "Total safety decisions made",
    labelnames=["allowed", "reason"],
)

decision_latency_ms = Histogram(
    "decision_latency_ms",
    "Latency from bar to decision in milliseconds",
)

reconcile_diffs_total = Counter(
    "reconcile_diffs_total",
    "Total reconciliation differences detected",
    labelnames=["kind"],
)

drift_z_score = Gauge(
    "drift_z_score",
    "Z-score of realized metric vs expected (turnover, etc.)",
    labelnames=["metric"],
)


def start_metrics_server(port: int = 9108) -> None:
    """Start the Prometheus HTTP metrics endpoint.

    Args:
        port: Port to listen on (default 9108).
    """
    start_http_server(port)


def reset_metrics_for_tests() -> None:
    """Clear all metrics from the registry (tests only).

    Use this between test runs to ensure clean state.
    """
    # Unregister all custom metrics by clearing the registry
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        with suppress(Exception):
            REGISTRY.unregister(collector)

    # Clear internal registry state to force fresh metrics
    REGISTRY._names_to_collectors.clear()
