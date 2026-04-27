"""Tests for mnq.venues — Phase 6 API boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from mnq.core.types import Side
from mnq.venues.base import (
    ConnectionState,
    OrderRequest,
    VenueAdapter,
    VenueFill,
    VenueType,
)
from mnq.venues.ninjatrader import NinjaTraderVenue, NTConfig

# ── NTConfig ───────────────────────────────────────────────────────────


class TestNTConfig:
    def test_defaults(self):
        cfg = NTConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 36973
        assert cfg.account == "Sim101"
        assert cfg.venue_type == VenueType.PAPER

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("NT_HOST", "192.168.1.1")
        monkeypatch.setenv("NT_PORT", "12345")
        monkeypatch.setenv("NT_ACCOUNT", "Live1")
        monkeypatch.setenv("NT_VENUE_TYPE", "live")
        cfg = NTConfig.from_env()
        assert cfg.host == "192.168.1.1"
        assert cfg.port == 12345
        assert cfg.account == "Live1"
        assert cfg.venue_type == VenueType.LIVE


# ── NinjaTraderVenue ───────────────────────────────────────────────────


class TestNinjaTraderVenue:
    def test_initial_state(self):
        venue = NinjaTraderVenue(NTConfig())
        assert venue.connection_state == ConnectionState.DISCONNECTED
        assert venue.venue_type == VenueType.PAPER

    @pytest.mark.asyncio
    async def test_connect_failure_sets_error(self):
        """Connect to non-existent port should raise and set ERROR state."""
        cfg = NTConfig(port=1, connect_timeout=0.5)
        venue = NinjaTraderVenue(cfg)
        with pytest.raises(ConnectionError):
            await venue.connect()
        assert venue.connection_state == ConnectionState.ERROR

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        """Disconnect when not connected should not raise."""
        venue = NinjaTraderVenue(NTConfig())
        await venue.disconnect()
        assert venue.connection_state == ConnectionState.DISCONNECTED

    def test_on_fill_registers_callback(self):
        venue = NinjaTraderVenue(NTConfig())
        cb = MagicMock()
        venue.on_fill(cb)
        assert cb in venue._fill_callbacks

    def test_on_disconnect_registers_callback(self):
        venue = NinjaTraderVenue(NTConfig())
        cb = MagicMock()
        venue.on_disconnect(cb)
        assert cb in venue._disconnect_callbacks


# ── VenueAdapter ABC ───────────────────────────────────────────────────


class TestVenueAdapterABC:
    def test_cannot_instantiate_abc(self):
        """VenueAdapter is abstract — can't be instantiated directly."""
        with pytest.raises(TypeError):
            VenueAdapter()

    def test_concrete_must_implement_all(self):
        """Subclass missing methods should fail."""

        class Incomplete(VenueAdapter):
            pass

        with pytest.raises(TypeError):
            Incomplete()


# ── OrderRequest ───────────────────────────────────────────────────────


class TestOrderRequest:
    def test_market_order(self):
        req = OrderRequest(
            client_order_id="test-123",
            symbol="MNQ",
            side=Side.LONG,
            order_type="market",
            qty=1,
        )
        assert req.price is None
        assert req.tif == "DAY"

    def test_limit_order(self):
        req = OrderRequest(
            client_order_id="test-456",
            symbol="MNQ",
            side=Side.SHORT,
            order_type="limit",
            qty=2,
            price=Decimal("20100.50"),
        )
        assert req.price == Decimal("20100.50")


# ── VenueFill ──────────────────────────────────────────────────────────


class TestVenueFill:
    def test_fill_fields(self):
        fill = VenueFill(
            client_order_id="c1",
            venue_order_id="v1",
            venue_fill_id="f1",
            price=Decimal("20000.00"),
            qty=1,
            side=Side.LONG,
            ts=datetime.now(tz=UTC),
        )
        assert fill.commission == Decimal("0")
        assert not fill.is_partial
