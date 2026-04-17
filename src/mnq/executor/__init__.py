"""Executor layer. Runtime concerns: order lifecycle, safety, reconciliation."""

from __future__ import annotations

from mnq.executor.orders import (
    Fill,
    Order,
    OrderBook,
    OrderError,
    OrderState,
    OrderType,
)
from mnq.executor.reconciler import (
    PeriodicReconciler,
    PositionReconciler,
    ReconcileDiff,
    ReconcileReport,
    VenueOrder,
    VenuePosition,
    VenueSnapshotFetcher,
)
from mnq.executor.safety import (
    CircuitBreaker,
    CompositeRiskCheck,
    FeatureStalenessCheck,
    KillSwitchFile,
    MarginBufferCheck,
    MaxDailyLossCheck,
    MaxOpenContractsCheck,
    PreTradeRiskCheck,
    RiskContext,
    SafetyDecision,
    SessionOpeningGuard,
)
from mnq.executor.venue_router import (
    RouterStats,
    VenueRouter,
)

__all__ = [
    # Orders
    "Order",
    "OrderBook",
    "OrderError",
    "OrderState",
    "OrderType",
    "Fill",
    # Reconciliation
    "PositionReconciler",
    "PeriodicReconciler",
    "ReconcileReport",
    "ReconcileDiff",
    "VenuePosition",
    "VenueOrder",
    "VenueSnapshotFetcher",
    # Safety
    "CircuitBreaker",
    "KillSwitchFile",
    "SafetyDecision",
    "RiskContext",
    "PreTradeRiskCheck",
    "MaxOpenContractsCheck",
    "MaxDailyLossCheck",
    "MarginBufferCheck",
    "SessionOpeningGuard",
    "FeatureStalenessCheck",
    "CompositeRiskCheck",
    # Venue Router
    "VenueRouter",
    "RouterStats",
]
