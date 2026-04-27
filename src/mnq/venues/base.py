"""Venue adapter ABC — Phase 6 API boundary.

Every broker/exchange implements this interface. The executor calls ONLY
these methods; it never touches broker-specific APIs directly.

The boundary enforces:
  1. Single point of integration — swap brokers by swapping venue adapters
  2. Type safety — all data flows through our types, not broker types
  3. Testability — SimVenue implements the same interface for paper trading
  4. Auditability — every outbound call is traceable via request_id

Lifecycle:
    venue = NinjaTraderVenue(config)
    await venue.connect()
    # ... submit orders, stream quotes ...
    await venue.disconnect()
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from mnq.core.types import Side


class ConnectionState(str, Enum):
    """Venue connection lifecycle state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class VenueType(str, Enum):
    """Broker classification."""

    SIM = "sim"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class QuoteTick:
    """One quote update from the venue."""

    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    ts: datetime


@dataclass(frozen=True)
class BarUpdate:
    """One completed bar from the venue."""

    symbol: str
    timeframe: str  # "1m", "5m", "1h", etc.
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    ts: datetime


@dataclass(frozen=True)
class OrderRequest:
    """Outbound order request to the venue."""

    client_order_id: str
    symbol: str
    side: Side
    order_type: str  # "market", "limit", "stop"
    qty: int
    price: Decimal | None = None  # Required for limit/stop
    stop_price: Decimal | None = None
    tif: str = "DAY"  # Time in force
    account: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderAck:
    """Venue acknowledgment of an order."""

    client_order_id: str
    venue_order_id: str
    status: str  # "working", "rejected"
    reject_reason: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now())


@dataclass(frozen=True)
class VenueFill:
    """Fill notification from the venue."""

    client_order_id: str
    venue_order_id: str
    venue_fill_id: str
    price: Decimal
    qty: int
    side: Side
    ts: datetime
    commission: Decimal = Decimal("0")
    is_partial: bool = False


@dataclass(frozen=True)
class CancelAck:
    """Venue acknowledgment of a cancel request."""

    client_order_id: str
    venue_order_id: str
    success: bool
    reason: str | None = None


@dataclass(frozen=True)
class AccountSnapshot:
    """Account state from the venue."""

    account_id: str
    equity: Decimal
    cash: Decimal
    margin_used: Decimal
    margin_available: Decimal
    unrealized_pnl: Decimal
    realized_pnl_today: Decimal
    ts: datetime


@dataclass(frozen=True)
class PositionSnapshot:
    """One open position from the venue."""

    symbol: str
    qty: int  # positive = long, negative = short
    avg_entry_price: Decimal
    unrealized_pnl: Decimal
    market_value: Decimal


class VenueAdapter(abc.ABC):
    """Abstract venue adapter — the API boundary.

    All methods are async to support both sync sim venues (wrapped in
    asyncio) and real async broker WebSocket APIs.
    """

    @property
    @abc.abstractmethod
    def venue_type(self) -> VenueType:
        """SIM, PAPER, or LIVE."""
        ...

    @property
    @abc.abstractmethod
    def connection_state(self) -> ConnectionState:
        """Current connection lifecycle state."""
        ...

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish connection to the venue. Idempotent."""
        ...

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect. Cancel open orders first if needed."""
        ...

    @abc.abstractmethod
    async def submit_order(self, request: OrderRequest) -> OrderAck:
        """Submit an order. Returns ack or rejection synchronously."""
        ...

    @abc.abstractmethod
    async def cancel_order(self, client_order_id: str, venue_order_id: str) -> CancelAck:
        """Request cancellation of a working order."""
        ...

    @abc.abstractmethod
    async def cancel_all(self, symbol: str | None = None) -> list[CancelAck]:
        """Cancel all open orders, optionally filtered by symbol."""
        ...

    @abc.abstractmethod
    async def get_account(self) -> AccountSnapshot:
        """Fetch current account snapshot."""
        ...

    @abc.abstractmethod
    async def get_positions(self) -> list[PositionSnapshot]:
        """Fetch all open positions."""
        ...

    @abc.abstractmethod
    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[QuoteTick]:
        """Stream live quotes. Yields QuoteTick objects."""
        ...

    @abc.abstractmethod
    async def stream_bars(self, symbol: str, timeframe: str = "1m") -> AsyncIterator[BarUpdate]:
        """Stream completed bars. Yields BarUpdate objects."""
        ...

    # ── Optional hooks (override if venue supports them) ──────

    async def heartbeat(self) -> bool:
        """Check if the connection is healthy. Default: always True."""
        return self.connection_state == ConnectionState.CONNECTED

    async def flatten(self, symbol: str | None = None) -> list[VenueFill]:
        """Close all positions for a symbol (or all symbols). Emergency use.

        Default implementation raises NotImplementedError.
        """
        raise NotImplementedError("Venue does not support flatten")

    def on_fill(self, callback: Callable[[VenueFill], None]) -> None:
        """Register a fill callback for async fill notifications.

        Default: no-op (venue must override to support push fills).
        """
        pass

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register a disconnect callback for reconnection logic.

        Default: no-op.
        """
        pass
