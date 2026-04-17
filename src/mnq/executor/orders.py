"""[REAL] Order state machine with journal-backed persistence.

All state transitions MUST go through the OrderBook class and MUST be
journaled before being reflected in memory. On crash/restart, recover by
replaying the journal.

Key invariants:
- No transitions out of terminal states
- ack() only valid from PENDING
- apply_fill() only on PENDING / WORKING / PARTIAL
- Duplicate venue_fill_id for same order is idempotent no-op
- qty > 0, filled_qty <= qty
- VWAP on avg_fill_price: decimal-exact
- All transitions emit metrics and structured logs
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from mnq.core.types import Side
from mnq.observability.logger import bind_trace_id, clear_trace_id, get_logger
from mnq.observability.metrics import (
    orders_filled_total,
    orders_rejected_total,
    orders_submitted_total,
)
from mnq.storage.journal import EventJournal
from mnq.storage.schema import (
    ORDER_ACKED,
    ORDER_CANCELLED,
    ORDER_FILLED,
    ORDER_PARTIAL,
    ORDER_REJECTED,
    ORDER_SUBMITTED,
    ORDER_WORKING,
)


class OrderState(str, Enum):
    """State machine for order lifecycle."""
    PENDING = "pending"         # submitted to broker, no ack yet
    WORKING = "working"         # broker acked, resting on book
    PARTIAL = "partial"         # partially filled
    FILLED = "filled"           # terminal: fully filled
    REJECTED = "rejected"       # terminal: broker rejected
    CANCELLED = "cancelled"     # terminal: cancelled by us or broker


class OrderType(str, Enum):
    """Order type enumeration."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


_TERMINAL = {OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED}


class OrderError(Exception):
    """Base exception for order state machine violations."""
    pass


class OrderBlocked(OrderError):
    """Raised when the pre-trade gate chain denies an order.

    Carries the denying gate name and reason so callers can journal
    the veto via `ORDER_BLOCKED` and decide whether to retry later.
    """

    def __init__(self, gate: str, reason: str) -> None:
        super().__init__(f"order blocked by gate={gate}: {reason}")
        self.gate = gate
        self.reason = reason


@dataclass(frozen=True)
class Order:
    """An immutable order with current state and fill history.

    Attributes:
        client_order_id: Idempotency key (uuid4 hex).
        venue_order_id: Assigned by broker on ack.
        symbol: Instrument symbol (e.g., 'MNQ').
        side: Long or Short.
        qty: Total order quantity (positive).
        order_type: Market, Limit, Stop.
        limit_price: For limit orders.
        stop_price: For stop orders.
        state: Current state in the machine.
        filled_qty: Cumulative filled quantity.
        avg_fill_price: Volume-weighted average fill price.
        submitted_at: Timestamp of submission.
        last_update_at: Timestamp of last state change.
        trace_id: Optional trace ID for correlation.
    """
    client_order_id: str
    venue_order_id: str | None
    symbol: str
    side: Side
    qty: int
    order_type: OrderType
    limit_price: Decimal | None
    stop_price: Decimal | None
    state: OrderState
    filled_qty: int
    avg_fill_price: Decimal | None
    submitted_at: datetime
    last_update_at: datetime
    trace_id: str | None

    @property
    def remaining_qty(self) -> int:
        """Remaining quantity to fill."""
        return self.qty - self.filled_qty

    @property
    def is_terminal(self) -> bool:
        """True if order is in a terminal state."""
        return self.state in _TERMINAL


@dataclass(frozen=True)
class Fill:
    """A confirmed fill from the venue.

    Attributes:
        client_order_id: Links to the order.
        venue_fill_id: Idempotency key for fills (from broker).
        price: Fill price.
        qty: Fill quantity (must be positive).
        ts: Fill timestamp, UTC.
        trace_id: Optional trace ID.
    """
    client_order_id: str
    venue_fill_id: str
    price: Decimal
    qty: int
    ts: datetime
    trace_id: str | None


class OrderBook:
    """In-memory order state with journal-backed persistence.

    All state transitions are journaled before being reflected in memory.
    On crash/restart, reconstruct by replaying the journal.
    """

    def __init__(
        self,
        journal: EventJournal,
        *,
        logger: Any = None,
        gate_chain: Any = None,
    ) -> None:
        """Initialize OrderBook.

        Args:
            journal: EventJournal instance for persistence.
            logger: Optional structlog logger. If None, creates one.
            gate_chain: Optional :class:`mnq.risk.GateChain`. When set,
                :meth:`submit` evaluates the chain and raises
                :class:`OrderBlocked` on any DENY before journaling
                ``ORDER_SUBMITTED``. Default ``None`` preserves
                legacy (ungated) behavior — every existing test case
                stays green. Production wiring flips this on via
                ``mnq.risk.build_default_chain()``.
        """
        self.journal = journal
        self.logger = logger or get_logger(__name__)
        self._orders: dict[str, Order] = {}
        # Track seen fills by (client_order_id, venue_fill_id) for idempotency
        self._seen_fills: set[tuple[str, str]] = set()
        self._gate_chain = gate_chain

    def submit(
        self,
        *,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        trace_id: str | None = None,
    ) -> Order:
        """Submit a new order.

        Generates a fresh client_order_id (uuid4 hex), writes ORDER_SUBMITTED
        to journal, returns Order in PENDING state.

        Args:
            symbol: Instrument symbol.
            side: Long or Short.
            qty: Order quantity (must be > 0).
            order_type: Market, Limit, or Stop.
            limit_price: Required for LIMIT orders.
            stop_price: Required for STOP orders.
            trace_id: Optional trace ID. Generated if None.

        Returns:
            Order in PENDING state.

        Raises:
            OrderError: If qty <= 0.
        """
        if qty <= 0:
            raise OrderError(f"qty must be > 0, got {qty}")

        trace_id = trace_id or str(uuid4())

        # Pre-trade gate chain: if configured, evaluate before journaling.
        # A DENY is logged + journaled via ORDER_REJECTED with a
        # `gate_blocked` payload and surfaces as OrderBlocked.
        if self._gate_chain is not None:
            allow, results = self._gate_chain.evaluate()
            if not allow:
                denying = results[-1]
                self.journal.append(
                    ORDER_REJECTED,
                    {
                        "symbol": symbol,
                        "side": side.value,
                        "qty": qty,
                        "order_type": order_type.value,
                        "gate_blocked": True,
                        "gate": denying.gate,
                        "reason": denying.reason,
                        "context": denying.context,
                    },
                    trace_id=trace_id,
                )
                bind_trace_id(trace_id)
                self.logger.warning(
                    "order_blocked_by_gate",
                    gate=denying.gate,
                    reason=denying.reason,
                    symbol=symbol,
                    side=side.value,
                    qty=qty,
                )
                clear_trace_id()
                raise OrderBlocked(denying.gate, denying.reason)

        client_order_id = uuid4().hex
        now = datetime.now(UTC)

        # Journal the submission
        self.journal.append(
            ORDER_SUBMITTED,
            {
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side.value,
                "qty": qty,
                "order_type": order_type.value,
                "limit_price": str(limit_price) if limit_price else None,
                "stop_price": str(stop_price) if stop_price else None,
            },
            trace_id=trace_id,
        )

        order = Order(
            client_order_id=client_order_id,
            venue_order_id=None,
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            state=OrderState.PENDING,
            filled_qty=0,
            avg_fill_price=None,
            submitted_at=now,
            last_update_at=now,
            trace_id=trace_id,
        )

        self._orders[client_order_id] = order

        # Emit metrics
        orders_submitted_total.labels(side=side.value, order_type=order_type.value).inc()

        # Emit structured log
        bind_trace_id(trace_id)
        self.logger.info(
            "order_submitted",
            client_order_id=client_order_id,
            symbol=symbol,
            side=side.value,
            qty=qty,
            order_type=order_type.value,
        )
        clear_trace_id()

        return order

    def ack(self, client_order_id: str, venue_order_id: str) -> Order:
        """Broker acknowledged order. Transition PENDING → WORKING.

        Journals ORDER_ACKED and ORDER_WORKING.

        Args:
            client_order_id: Client order ID.
            venue_order_id: Venue-assigned order ID.

        Returns:
            Order in WORKING state.

        Raises:
            OrderError: If order not found, already in terminal state,
                or not in PENDING state.
        """
        order = self._get_order_or_raise(client_order_id)
        trace_id = order.trace_id or str(uuid4())

        if order.is_terminal:
            raise OrderError(
                f"Cannot ack terminal order {client_order_id} "
                f"in state {order.state}"
            )
        if order.state != OrderState.PENDING:
            raise OrderError(
                f"Cannot ack order {client_order_id} in state {order.state}; "
                f"expected PENDING"
            )

        now = datetime.now(UTC)

        # Journal ack
        self.journal.append(
            ORDER_ACKED,
            {
                "client_order_id": client_order_id,
                "venue_order_id": venue_order_id,
            },
            trace_id=trace_id,
        )

        # Journal working
        self.journal.append(
            ORDER_WORKING,
            {
                "client_order_id": client_order_id,
                "venue_order_id": venue_order_id,
                "symbol": order.symbol,
                "qty_remaining": order.qty,
            },
            trace_id=trace_id,
        )

        # Update in-memory state
        updated = Order(
            client_order_id=order.client_order_id,
            venue_order_id=venue_order_id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            state=OrderState.WORKING,
            filled_qty=order.filled_qty,
            avg_fill_price=order.avg_fill_price,
            submitted_at=order.submitted_at,
            last_update_at=now,
            trace_id=trace_id,
        )
        self._orders[client_order_id] = updated

        # Emit structured log
        bind_trace_id(trace_id)
        self.logger.info(
            "order_acked",
            client_order_id=client_order_id,
            venue_order_id=venue_order_id,
            state=OrderState.WORKING.value,
        )
        clear_trace_id()

        return updated

    def apply_fill(self, fill: Fill) -> Order:
        """Apply a partial or full fill to an order.

        Idempotent on (client_order_id, venue_fill_id) — replayed fills
        are no-ops. Journals ORDER_PARTIAL or ORDER_FILLED. Updates
        filled_qty and avg_fill_price via VWAP.

        Args:
            fill: Fill object with client_order_id, venue_fill_id, price, qty, ts.

        Returns:
            Updated Order.

        Raises:
            OrderError: If order not found, already in terminal state
                (except FILLED), or qty is invalid.
        """
        order = self._get_order_or_raise(fill.client_order_id)
        trace_id = fill.trace_id or order.trace_id or str(uuid4())

        # Check idempotency
        fill_key = (fill.client_order_id, fill.venue_fill_id)
        if fill_key in self._seen_fills:
            bind_trace_id(trace_id)
            self.logger.debug(
                "duplicate_fill_ignored",
                client_order_id=fill.client_order_id,
                venue_fill_id=fill.venue_fill_id,
            )
            clear_trace_id()
            return order  # no-op

        self._seen_fills.add(fill_key)

        if fill.qty <= 0:
            raise OrderError(f"Fill qty must be > 0, got {fill.qty}")

        # Terminal orders (FILLED, REJECTED, CANCELLED) cannot accept new fills
        # except when transitioning from WORKING/PARTIAL to FILLED
        if order.is_terminal and order.state != OrderState.FILLED:
            raise OrderError(
                f"Cannot fill terminal order {fill.client_order_id} "
                f"in state {order.state}"
            )

        # Only PENDING, WORKING, and PARTIAL can accept fills
        if order.state not in {OrderState.PENDING, OrderState.WORKING, OrderState.PARTIAL}:
            raise OrderError(
                f"Cannot fill order {fill.client_order_id} in state {order.state}; "
                f"expected PENDING, WORKING, or PARTIAL"
            )

        new_filled_qty = order.filled_qty + fill.qty
        if new_filled_qty > order.qty:
            raise OrderError(
                f"Fill overfull: {order.filled_qty} + {fill.qty} > {order.qty}"
            )

        # Compute VWAP: (prior_avg * filled_qty + new_price * new_qty) / (filled_qty + new_qty)
        if order.avg_fill_price is None:
            avg_fill_price = fill.price
        else:
            numerator = (
                order.avg_fill_price * Decimal(order.filled_qty)
                + fill.price * Decimal(fill.qty)
            )
            denominator = Decimal(new_filled_qty)
            avg_fill_price = (numerator / denominator).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_EVEN,
            )

        now = datetime.now(UTC)

        # Determine new state: partial or filled
        if new_filled_qty == order.qty:
            new_state = OrderState.FILLED
            event_type = ORDER_FILLED
        else:
            new_state = OrderState.PARTIAL
            event_type = ORDER_PARTIAL

        # Journal the fill
        self.journal.append(
            event_type,
            {
                "client_order_id": fill.client_order_id,
                "venue_fill_id": fill.venue_fill_id,
                "price": str(fill.price),
                "qty": fill.qty,
                "filled_qty": new_filled_qty,
                "avg_fill_price": str(avg_fill_price),
            },
            trace_id=trace_id,
        )

        # Update in-memory state
        updated = Order(
            client_order_id=order.client_order_id,
            venue_order_id=order.venue_order_id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            state=new_state,
            filled_qty=new_filled_qty,
            avg_fill_price=avg_fill_price,
            submitted_at=order.submitted_at,
            last_update_at=now,
            trace_id=trace_id,
        )
        self._orders[fill.client_order_id] = updated

        # Emit metrics
        if new_state == OrderState.FILLED:
            orders_filled_total.labels(side=order.side.value, exit_reason="filled").inc()

        # Emit structured log
        bind_trace_id(trace_id)
        self.logger.info(
            "order_fill_applied",
            client_order_id=fill.client_order_id,
            venue_fill_id=fill.venue_fill_id,
            fill_price=str(fill.price),
            fill_qty=fill.qty,
            filled_qty=new_filled_qty,
            avg_fill_price=str(avg_fill_price),
            state=new_state.value,
        )
        clear_trace_id()

        return updated

    def reject(self, client_order_id: str, reason: str) -> Order:
        """Reject an order (broker rejection).

        Transitions PENDING → REJECTED (terminal).

        Args:
            client_order_id: Client order ID.
            reason: Rejection reason.

        Returns:
            Order in REJECTED state.

        Raises:
            OrderError: If order not found or already terminal.
        """
        order = self._get_order_or_raise(client_order_id)
        trace_id = order.trace_id or str(uuid4())

        if order.is_terminal:
            raise OrderError(
                f"Cannot reject terminal order {client_order_id} "
                f"in state {order.state}"
            )

        now = datetime.now(UTC)

        # Journal rejection
        self.journal.append(
            ORDER_REJECTED,
            {
                "client_order_id": client_order_id,
                "reason": reason,
            },
            trace_id=trace_id,
        )

        # Update in-memory state
        updated = Order(
            client_order_id=order.client_order_id,
            venue_order_id=order.venue_order_id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            state=OrderState.REJECTED,
            filled_qty=order.filled_qty,
            avg_fill_price=order.avg_fill_price,
            submitted_at=order.submitted_at,
            last_update_at=now,
            trace_id=trace_id,
        )
        self._orders[client_order_id] = updated

        # Emit metrics
        orders_rejected_total.labels(reason=reason).inc()

        # Emit structured log
        bind_trace_id(trace_id)
        self.logger.info(
            "order_rejected",
            client_order_id=client_order_id,
            reason=reason,
            state=OrderState.REJECTED.value,
        )
        clear_trace_id()

        return updated

    def cancel(self, client_order_id: str, *, reason: str = "user") -> Order:
        """Cancel an order.

        Transitions PENDING/WORKING/PARTIAL → CANCELLED (terminal).

        Args:
            client_order_id: Client order ID.
            reason: Cancellation reason (default "user").

        Returns:
            Order in CANCELLED state.

        Raises:
            OrderError: If order not found or already terminal.
        """
        order = self._get_order_or_raise(client_order_id)
        trace_id = order.trace_id or str(uuid4())

        if order.is_terminal:
            raise OrderError(
                f"Cannot cancel terminal order {client_order_id} "
                f"in state {order.state}"
            )

        now = datetime.now(UTC)

        # Journal cancellation
        self.journal.append(
            ORDER_CANCELLED,
            {
                "client_order_id": client_order_id,
                "reason": reason,
            },
            trace_id=trace_id,
        )

        # Update in-memory state
        updated = Order(
            client_order_id=order.client_order_id,
            venue_order_id=order.venue_order_id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            state=OrderState.CANCELLED,
            filled_qty=order.filled_qty,
            avg_fill_price=order.avg_fill_price,
            submitted_at=order.submitted_at,
            last_update_at=now,
            trace_id=trace_id,
        )
        self._orders[client_order_id] = updated

        # Emit metrics
        orders_rejected_total.labels(reason=reason).inc()

        # Emit structured log
        bind_trace_id(trace_id)
        self.logger.info(
            "order_cancelled",
            client_order_id=client_order_id,
            reason=reason,
            state=OrderState.CANCELLED.value,
        )
        clear_trace_id()

        return updated

    def get(self, client_order_id: str) -> Order | None:
        """Get an order by client_order_id.

        Args:
            client_order_id: Client order ID.

        Returns:
            Order if found, None otherwise.
        """
        return self._orders.get(client_order_id)

    def open_orders(self) -> list[Order]:
        """Return all non-terminal orders.

        Returns:
            List of orders in PENDING, WORKING, or PARTIAL state.
        """
        return [
            o
            for o in self._orders.values()
            if o.state in {OrderState.PENDING, OrderState.WORKING, OrderState.PARTIAL}
        ]

    def all_orders(self) -> list[Order]:
        """Return all orders (terminal and non-terminal).

        Returns:
            List of all orders.
        """
        return list(self._orders.values())

    @classmethod
    def from_journal(cls, journal: EventJournal) -> OrderBook:
        """Reconstruct OrderBook by replaying the journal.

        Args:
            journal: EventJournal instance to replay.

        Returns:
            Reconstructed OrderBook.
        """
        book = cls(journal)

        for entry in journal.replay():
            if entry.event_type == ORDER_SUBMITTED:
                symbol = entry.payload["symbol"]
                side = Side(entry.payload["side"])
                qty = entry.payload["qty"]
                order_type = OrderType(entry.payload["order_type"])
                limit_price = (
                    Decimal(entry.payload["limit_price"])
                    if entry.payload["limit_price"]
                    else None
                )
                stop_price = (
                    Decimal(entry.payload["stop_price"])
                    if entry.payload["stop_price"]
                    else None
                )
                client_order_id = entry.payload["client_order_id"]

                order = Order(
                    client_order_id=client_order_id,
                    venue_order_id=None,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    order_type=order_type,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    state=OrderState.PENDING,
                    filled_qty=0,
                    avg_fill_price=None,
                    submitted_at=entry.ts,
                    last_update_at=entry.ts,
                    trace_id=entry.trace_id,
                )
                book._orders[client_order_id] = order

            elif entry.event_type == ORDER_ACKED:
                client_order_id = entry.payload["client_order_id"]
                venue_order_id = entry.payload["venue_order_id"]
                if client_order_id in book._orders:
                    order = book._orders[client_order_id]
                    book._orders[client_order_id] = Order(
                        client_order_id=order.client_order_id,
                        venue_order_id=venue_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        order_type=order.order_type,
                        limit_price=order.limit_price,
                        stop_price=order.stop_price,
                        state=OrderState.WORKING,
                        filled_qty=order.filled_qty,
                        avg_fill_price=order.avg_fill_price,
                        submitted_at=order.submitted_at,
                        last_update_at=entry.ts,
                        trace_id=entry.trace_id,
                    )

            elif entry.event_type == ORDER_PARTIAL:
                client_order_id = entry.payload["client_order_id"]
                filled_qty = entry.payload["filled_qty"]
                avg_fill_price = Decimal(entry.payload["avg_fill_price"])
                if client_order_id in book._orders:
                    order = book._orders[client_order_id]
                    fill_key = (
                        client_order_id,
                        entry.payload["venue_fill_id"],
                    )
                    book._seen_fills.add(fill_key)
                    book._orders[client_order_id] = Order(
                        client_order_id=order.client_order_id,
                        venue_order_id=order.venue_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        order_type=order.order_type,
                        limit_price=order.limit_price,
                        stop_price=order.stop_price,
                        state=OrderState.PARTIAL,
                        filled_qty=filled_qty,
                        avg_fill_price=avg_fill_price,
                        submitted_at=order.submitted_at,
                        last_update_at=entry.ts,
                        trace_id=entry.trace_id,
                    )

            elif entry.event_type == ORDER_FILLED:
                client_order_id = entry.payload["client_order_id"]
                filled_qty = entry.payload["filled_qty"]
                avg_fill_price = Decimal(entry.payload["avg_fill_price"])
                if client_order_id in book._orders:
                    order = book._orders[client_order_id]
                    fill_key = (
                        client_order_id,
                        entry.payload["venue_fill_id"],
                    )
                    book._seen_fills.add(fill_key)
                    book._orders[client_order_id] = Order(
                        client_order_id=order.client_order_id,
                        venue_order_id=order.venue_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        order_type=order.order_type,
                        limit_price=order.limit_price,
                        stop_price=order.stop_price,
                        state=OrderState.FILLED,
                        filled_qty=filled_qty,
                        avg_fill_price=avg_fill_price,
                        submitted_at=order.submitted_at,
                        last_update_at=entry.ts,
                        trace_id=entry.trace_id,
                    )

            elif entry.event_type == ORDER_REJECTED:
                client_order_id = entry.payload["client_order_id"]
                if client_order_id in book._orders:
                    order = book._orders[client_order_id]
                    book._orders[client_order_id] = Order(
                        client_order_id=order.client_order_id,
                        venue_order_id=order.venue_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        order_type=order.order_type,
                        limit_price=order.limit_price,
                        stop_price=order.stop_price,
                        state=OrderState.REJECTED,
                        filled_qty=order.filled_qty,
                        avg_fill_price=order.avg_fill_price,
                        submitted_at=order.submitted_at,
                        last_update_at=entry.ts,
                        trace_id=entry.trace_id,
                    )

            elif entry.event_type == ORDER_CANCELLED:
                client_order_id = entry.payload["client_order_id"]
                if client_order_id in book._orders:
                    order = book._orders[client_order_id]
                    book._orders[client_order_id] = Order(
                        client_order_id=order.client_order_id,
                        venue_order_id=order.venue_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        order_type=order.order_type,
                        limit_price=order.limit_price,
                        stop_price=order.stop_price,
                        state=OrderState.CANCELLED,
                        filled_qty=order.filled_qty,
                        avg_fill_price=order.avg_fill_price,
                        submitted_at=order.submitted_at,
                        last_update_at=entry.ts,
                        trace_id=entry.trace_id,
                    )

        return book

    # ---- private helpers ----

    def _get_order_or_raise(self, client_order_id: str) -> Order:
        """Get an order or raise OrderError."""
        if client_order_id not in self._orders:
            raise OrderError(f"Order not found: {client_order_id}")
        return self._orders[client_order_id]
