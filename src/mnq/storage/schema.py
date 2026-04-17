"""[REAL] Event type constants and payload schemas for the event journal.

Defines the canonical event types that can be logged and provides TypedDict
schemas for the payload structure of each event. Payloads are stored as JSON
and must be JSON-serializable dicts.
"""

from __future__ import annotations

from typing import Any, TypedDict

# Event type constants
ORDER_SUBMITTED = "order.submitted"
ORDER_ACKED = "order.acked"
ORDER_WORKING = "order.working"
ORDER_PARTIAL = "order.partial_fill"
ORDER_FILLED = "order.filled"
ORDER_REJECTED = "order.rejected"
ORDER_CANCELLED = "order.cancelled"
POSITION_UPDATE = "position.update"
PNL_UPDATE = "pnl.update"
FEATURE_STALENESS = "feature.staleness"
SAFETY_DECISION = "safety.decision"
WS_CONNECT = "ws.connect"
WS_DISCONNECT = "ws.disconnect"
WS_GAP_DETECTED = "ws.gap"
RECONCILE_START = "reconcile.start"
RECONCILE_DIFF = "reconcile.diff"
RECONCILE_OK = "reconcile.ok"
RECONCILE_HALT = "reconcile.halt"
FILL_EXPECTED = "fill.expected"
FILL_REALIZED = "fill.realized"
FILL_ORPHANED = "fill.orphaned"
ROLL_SCHEDULED = "roll.scheduled"
ROLL_WARNING = "roll.warning"
ROLL_STARTED = "roll.started"
ROLL_COMPLETED = "roll.completed"
DRIFT_ALERT = "drift.alert"
DRIFT_OK = "drift.ok"


# Payload TypedDicts for common events
class OrderSubmittedPayload(TypedDict, total=False):
    """Order submission event payload."""
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float


class OrderAckedPayload(TypedDict, total=False):
    """Order acknowledged by exchange event payload."""
    order_id: str
    exchange_id: str


class OrderWorkingPayload(TypedDict, total=False):
    """Order in market event payload."""
    order_id: str
    symbol: str
    quantity_remaining: int


class OrderPartialFillPayload(TypedDict, total=False):
    """Partial fill event payload."""
    order_id: str
    filled_quantity: int
    fill_price: float
    cumulative_quantity: int


class OrderFilledPayload(TypedDict, total=False):
    """Order completely filled event payload."""
    order_id: str
    total_quantity: int
    avg_price: float


class OrderRejectedPayload(TypedDict, total=False):
    """Order rejected event payload."""
    order_id: str
    reason: str


class OrderCancelledPayload(TypedDict, total=False):
    """Order cancelled event payload."""
    order_id: str
    reason: str


class PositionUpdatePayload(TypedDict, total=False):
    """Position update event payload."""
    symbol: str
    quantity: int
    avg_entry: float


class PnlUpdatePayload(TypedDict, total=False):
    """P&L update event payload."""
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float


class FeatureStalenessPayload(TypedDict, total=False):
    """Feature staleness detection event payload."""
    feature_name: str
    age_seconds: float


class SafetyDecisionPayload(TypedDict, total=False):
    """Safety system decision event payload."""
    decision: str
    reason: str


class WsConnectPayload(TypedDict, total=False):
    """WebSocket connection event payload."""
    endpoint: str
    timestamp: str


class WsDisconnectPayload(TypedDict, total=False):
    """WebSocket disconnection event payload."""
    endpoint: str
    reason: str | None


class WsGapDetectedPayload(TypedDict, total=False):
    """WebSocket message gap detection event payload."""
    endpoint: str
    expected_seq: int
    actual_seq: int


class ReconcileStartPayload(TypedDict, total=False):
    """Reconciliation start event payload."""
    scope: str


class ReconcileDiffPayload(TypedDict, total=False):
    """Reconciliation difference detected event payload."""
    scope: str
    field: str
    local_value: Any
    remote_value: Any


class FillExpectedPayload(TypedDict, total=False):
    """Fill expectation at order submission."""
    order_id: str
    symbol: str
    side: str
    qty: int
    expected_price: str
    reference_bid: str
    reference_ask: str
    spread_ticks: float
    volatility_regime: str
    tod_bucket: str
    liquidity_proxy: float
    tick_size: str


class FillRealizedPayload(TypedDict, total=False):
    """Realized fill record (matched with expectation)."""
    order_id: str
    realized_price: str
    fill_qty: int
    slippage_ticks: float
    latency_ms: float


class FillOrphanedPayload(TypedDict, total=False):
    """Fill with no matching expectation or expired expectation."""
    order_id: str
    reason: str  # "no_matching_expectation" or "timeout"
