"""Venue router — bridges OrderBook to VenueAdapter.

The OrderBook manages order state and journaling. The VenueAdapter talks
to the broker. This router connects them:

  1. OrderBook.submit() creates Order in PENDING state
  2. VenueRouter forwards to VenueAdapter.submit_order()
  3. On ack → OrderBook.ack()
  4. On fill → OrderBook.apply_fill()
  5. On reject → OrderBook.reject()

The router also:
  - Handles async fill notifications from the venue
  - Manages the reconciliation loop
  - Provides shadow mode (log decisions but don't send to venue)

Usage:
    venue = NinjaTraderVenue(config)
    await venue.connect()
    router = VenueRouter(order_book, venue, shadow=True)
    order = router.submit_order(symbol, side, qty, order_type)
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from decimal import Decimal

from mnq.core.types import Side
from mnq.executor.orders import (
    Fill,
    Order,
    OrderBlocked,
    OrderBook,
    OrderState,
    OrderType,
)
from mnq.venues.base import (
    OrderRequest,
    VenueAdapter,
    VenueFill,
    VenueType,
)

logger = logging.getLogger(__name__)


@dataclass
class RouterStats:
    """Accumulated routing statistics."""

    orders_routed: int = 0
    orders_acked: int = 0
    orders_rejected: int = 0
    orders_filled: int = 0
    orders_blocked: int = 0
    shadow_suppressed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "routed": self.orders_routed,
            "acked": self.orders_acked,
            "rejected": self.orders_rejected,
            "filled": self.orders_filled,
            "blocked": self.orders_blocked,
            "shadow_suppressed": self.shadow_suppressed,
        }


class VenueRouter:
    """Routes orders from OrderBook to VenueAdapter.

    In shadow mode, orders are journaled and tracked but never sent to
    the venue. This allows running the full decision pipeline against
    live data without risk.

    Args:
        order_book: The order state machine + journal.
        venue: The broker/exchange adapter.
        shadow: If True, don't actually send orders to venue.
    """

    def __init__(
        self,
        order_book: OrderBook,
        venue: VenueAdapter,
        *,
        shadow: bool = False,
    ):
        self.order_book = order_book
        self.venue = venue
        self.shadow = shadow
        self.stats = RouterStats()
        self._pending_orders: dict[str, Order] = {}

        # Register for async fill notifications
        venue.on_fill(self._on_venue_fill)

    @property
    def is_shadow(self) -> bool:
        return self.shadow

    @property
    def venue_type(self) -> VenueType:
        return self.venue.venue_type

    async def submit_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> Order:
        """Submit an order through the full pipeline.

        1. OrderBook.submit() → gate chain + journal
        2. If not shadow: send to venue
        3. Track for async fill handling

        Raises:
            OrderBlocked: If gate chain denies the order.
        """
        # Step 1: Create order in OrderBook (runs gate chain, journals)
        try:
            order = self.order_book.submit(
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
            )
        except OrderBlocked:
            self.stats.orders_blocked += 1
            raise

        self.stats.orders_routed += 1

        # Step 2: Route to venue (or suppress in shadow mode)
        if self.shadow:
            self.stats.shadow_suppressed += 1
            logger.info(
                "shadow_mode: order %s suppressed (would have been %s %d %s @ %s)",
                order.client_order_id,
                side.value,
                qty,
                symbol,
                order_type.value,
            )
            # In shadow mode, simulate immediate ack
            shadow_venue_id = f"shadow-{order.client_order_id[:8]}"
            self.order_book.ack(order.client_order_id, shadow_venue_id)
            self.stats.orders_acked += 1
            return self.order_book.get(order.client_order_id)

        # Real venue submission
        request = OrderRequest(
            client_order_id=order.client_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type.value,
            qty=qty,
            price=limit_price,
            stop_price=stop_price,
        )

        self._pending_orders[order.client_order_id] = order

        try:
            ack = await self.venue.submit_order(request)
        except Exception as exc:
            logger.error(
                "venue_submit_failed: order=%s error=%s",
                order.client_order_id,
                exc,
            )
            # Reject the order in the book
            self.order_book.reject(
                order.client_order_id,
                reason=f"venue error: {exc}",
            )
            self.stats.orders_rejected += 1
            self._pending_orders.pop(order.client_order_id, None)
            return self.order_book.get(order.client_order_id)

        # Process ack
        if ack.status == "rejected":
            self.order_book.reject(
                order.client_order_id,
                reason=ack.reject_reason or "venue rejected",
            )
            self.stats.orders_rejected += 1
            self._pending_orders.pop(order.client_order_id, None)
        else:
            self.order_book.ack(order.client_order_id, ack.venue_order_id)
            self.stats.orders_acked += 1

        return self.order_book.get(order.client_order_id)

    async def cancel_order(self, client_order_id: str) -> Order:
        """Cancel an order through the venue."""
        order = self.order_book.get(client_order_id)
        if order.state in {OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED}:
            return order

        if self.shadow:
            self.order_book.cancel(client_order_id, reason="shadow cancel")
            return self.order_book.get(client_order_id)

        if order.venue_order_id:
            ack = await self.venue.cancel_order(client_order_id, order.venue_order_id)
            if ack.success:
                self.order_book.cancel(client_order_id, reason="user cancel")
        else:
            self.order_book.cancel(client_order_id, reason="no venue id")

        return self.order_book.get(client_order_id)

    async def cancel_all(self, symbol: str | None = None) -> None:
        """Cancel all open orders."""
        if not self.shadow:
            await self.venue.cancel_all(symbol)

        for oid, order in list(self._pending_orders.items()):
            if symbol is None or order.symbol == symbol:
                with contextlib.suppress(Exception):
                    self.order_book.cancel(oid, reason="cancel_all")
        self._pending_orders.clear()

    async def flatten(self, symbol: str | None = None) -> None:
        """Emergency: cancel all orders + close all positions."""
        await self.cancel_all(symbol)
        if not self.shadow:
            await self.venue.flatten(symbol)
        logger.warning("FLATTEN executed (shadow=%s, symbol=%s)", self.shadow, symbol)

    def _on_venue_fill(self, venue_fill: VenueFill) -> None:
        """Handle async fill notification from venue."""
        try:
            fill = Fill(
                client_order_id=venue_fill.client_order_id,
                venue_fill_id=venue_fill.venue_fill_id,
                price=venue_fill.price,
                qty=venue_fill.qty,
                ts=venue_fill.ts,
            )
            self.order_book.apply_fill(fill)
            self.stats.orders_filled += 1

            order = self.order_book.get(venue_fill.client_order_id)
            if order.state == OrderState.FILLED:
                self._pending_orders.pop(venue_fill.client_order_id, None)

            logger.info(
                "fill_applied: order=%s price=%s qty=%d",
                venue_fill.client_order_id,
                venue_fill.price,
                venue_fill.qty,
            )
        except Exception:
            logger.exception(
                "fill_apply_failed: order=%s",
                venue_fill.client_order_id,
            )
