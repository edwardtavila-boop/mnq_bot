"""Tests for mnq.executor.venue_router — venue-executor bridge."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mnq.core.types import Side
from mnq.executor.orders import OrderBook, OrderState, OrderType
from mnq.executor.venue_router import RouterStats, VenueRouter
from mnq.venues.base import (
    ConnectionState,
    OrderAck,
    VenueFill,
    VenueType,
)


# ── Fixtures ───────────────────────────────────────────────────────────

class MockVenue:
    """Minimal mock venue for testing the router."""

    def __init__(self):
        self.venue_type = VenueType.PAPER
        self.connection_state = ConnectionState.CONNECTED
        self._fill_callbacks = []
        self._disconnect_callbacks = []
        self.submitted = []
        self.cancelled = []

    async def submit_order(self, request):
        self.submitted.append(request)
        return OrderAck(
            client_order_id=request.client_order_id,
            venue_order_id=f"venue-{request.client_order_id[:8]}",
            status="working",
            ts=datetime.now(tz=UTC),
        )

    async def cancel_order(self, client_order_id, venue_order_id):
        self.cancelled.append(client_order_id)
        from mnq.venues.base import CancelAck
        return CancelAck(
            client_order_id=client_order_id,
            venue_order_id=venue_order_id,
            success=True,
        )

    async def cancel_all(self, symbol=None):
        return []

    async def flatten(self, symbol=None):
        return []

    def on_fill(self, callback):
        self._fill_callbacks.append(callback)

    def on_disconnect(self, callback):
        self._disconnect_callbacks.append(callback)


def _make_order_book():
    """Create a minimal OrderBook with mocked journal."""
    journal = MagicMock()
    journal.append = MagicMock()
    return OrderBook(journal=journal, gate_chain=None)


# ── RouterStats ────────────────────────────────────────────────────────

class TestRouterStats:
    def test_initial_zeros(self):
        stats = RouterStats()
        assert stats.orders_routed == 0
        assert stats.as_dict()["routed"] == 0

    def test_as_dict(self):
        stats = RouterStats(orders_routed=5, shadow_suppressed=3)
        d = stats.as_dict()
        assert d["routed"] == 5
        assert d["shadow_suppressed"] == 3


# ── VenueRouter shadow mode ───────────────────────────────────────────

class TestVenueRouterShadow:
    @pytest.mark.asyncio
    async def test_shadow_suppresses_venue_call(self):
        book = _make_order_book()
        venue = MockVenue()
        router = VenueRouter(book, venue, shadow=True)

        order = await router.submit_order("MNQ", Side.LONG, 1, OrderType.MARKET)

        assert router.is_shadow
        assert len(venue.submitted) == 0  # Never hit venue
        assert router.stats.shadow_suppressed == 1
        assert router.stats.orders_routed == 1

    @pytest.mark.asyncio
    async def test_shadow_still_journals(self):
        book = _make_order_book()
        venue = MockVenue()
        router = VenueRouter(book, venue, shadow=True)

        order = await router.submit_order("MNQ", Side.LONG, 1, OrderType.MARKET)

        # OrderBook.submit was called (journals the submission)
        assert book.journal.append.called


# ── VenueRouter live mode ─────────────────────────────────────────────

class TestVenueRouterLive:
    @pytest.mark.asyncio
    async def test_live_sends_to_venue(self):
        book = _make_order_book()
        venue = MockVenue()
        router = VenueRouter(book, venue, shadow=False)

        order = await router.submit_order("MNQ", Side.LONG, 1, OrderType.MARKET)

        assert len(venue.submitted) == 1
        assert router.stats.orders_acked == 1

    @pytest.mark.asyncio
    async def test_venue_reject_propagates(self):
        book = _make_order_book()
        venue = MockVenue()

        # Override to return reject
        async def reject_order(request):
            return OrderAck(
                client_order_id=request.client_order_id,
                venue_order_id="",
                status="rejected",
                reject_reason="insufficient margin",
                ts=datetime.now(tz=UTC),
            )
        venue.submit_order = reject_order
        router = VenueRouter(book, venue, shadow=False)

        order = await router.submit_order("MNQ", Side.LONG, 1, OrderType.MARKET)
        assert router.stats.orders_rejected == 1
