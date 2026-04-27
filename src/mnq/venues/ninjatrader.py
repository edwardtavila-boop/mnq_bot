"""NinjaTrader 8 venue adapter — Phase 6 API boundary implementation.

NinjaTrader 8 exposes the ATI (Automated Trading Interface) on
localhost:36973 by default. It supports:
  - PLACE: Submit market/limit/stop orders
  - CANCEL: Cancel an order by order_id
  - CANCELALLORDERS: Flatten all
  - CLOSEPOSITION: Close a specific position
  - POSITIONS: Query open positions
  - ORDERS: Query active orders
  - MARKETDATA: Subscribe to tick data

Connection flow:
  1. NinjaTrader 8 must be running with ATI enabled
  2. We connect via TCP socket to localhost:36973
  3. Commands are newline-delimited text
  4. Responses are newline-delimited text

This adapter wraps the raw ATI protocol into our VenueAdapter ABC.
If NinjaTrader isn't running or ATI isn't enabled, connect() will fail
gracefully and set connection_state to ERROR.

IMPORTANT: This is the Phase 6 scaffold. Full ATI message parsing
requires iterating on the actual NinjaTrader responses during shadow
trading (Phase 8). The structure is built to be filled in incrementally.

Config via environment variables:
  NT_HOST=127.0.0.1 (default)
  NT_PORT=36973 (default ATI port)
  NT_ACCOUNT=Sim101 (default paper account)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from mnq.core.types import Side
from mnq.venues.base import (
    AccountSnapshot,
    BarUpdate,
    CancelAck,
    ConnectionState,
    OrderAck,
    OrderRequest,
    PositionSnapshot,
    QuoteTick,
    VenueAdapter,
    VenueFill,
    VenueType,
)

logger = logging.getLogger(__name__)


@dataclass
class NTConfig:
    """NinjaTrader connection configuration."""

    host: str = "127.0.0.1"
    port: int = 36973
    account: str = "Sim101"
    connect_timeout: float = 10.0
    read_timeout: float = 5.0
    reconnect_delay: float = 5.0
    max_reconnect_attempts: int = 5
    venue_type: VenueType = VenueType.PAPER  # Override to LIVE for real money

    @classmethod
    def from_env(cls) -> NTConfig:
        """Load config from environment variables."""
        return cls(
            host=os.environ.get("NT_HOST", "127.0.0.1"),
            port=int(os.environ.get("NT_PORT", "36973")),
            account=os.environ.get("NT_ACCOUNT", "Sim101"),
            venue_type=VenueType(os.environ.get("NT_VENUE_TYPE", "paper")),
        )


class NinjaTraderVenue(VenueAdapter):
    """NinjaTrader 8 ATI venue adapter.

    Implements the VenueAdapter ABC for NinjaTrader's localhost ATI.
    """

    def __init__(self, config: NTConfig | None = None):
        self.config = config or NTConfig.from_env()
        self._state = ConnectionState.DISCONNECTED
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._fill_callbacks: list[Callable[[VenueFill], None]] = []
        self._disconnect_callbacks: list[Callable[[], None]] = []
        self._order_map: dict[str, str] = {}  # client_order_id → venue_order_id
        self._reconnect_attempts = 0
        self._listen_task: asyncio.Task | None = None

    @property
    def venue_type(self) -> VenueType:
        return self.config.venue_type

    @property
    def connection_state(self) -> ConnectionState:
        return self._state

    async def connect(self) -> None:
        """Connect to NinjaTrader ATI socket."""
        if self._state == ConnectionState.CONNECTED:
            return

        self._state = ConnectionState.CONNECTING
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.host, self.config.port),
                timeout=self.config.connect_timeout,
            )
            self._state = ConnectionState.CONNECTED
            self._reconnect_attempts = 0
            logger.info(
                "Connected to NinjaTrader ATI at %s:%d (account=%s, type=%s)",
                self.config.host,
                self.config.port,
                self.config.account,
                self.config.venue_type.value,
            )

            # Start background listener for async fills/updates
            self._listen_task = asyncio.create_task(self._listen_loop())

        except (TimeoutError, ConnectionRefusedError, OSError) as exc:
            self._state = ConnectionState.ERROR
            logger.error(
                "Failed to connect to NinjaTrader ATI at %s:%d: %s",
                self.config.host,
                self.config.port,
                exc,
            )
            raise ConnectionError(
                f"NinjaTrader ATI not available at "
                f"{self.config.host}:{self.config.port}. "
                f"Ensure NinjaTrader 8 is running with ATI enabled."
            ) from exc

    async def disconnect(self) -> None:
        """Gracefully disconnect from NinjaTrader ATI."""
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task

        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()

        self._reader = None
        self._writer = None
        self._state = ConnectionState.DISCONNECTED
        logger.info("Disconnected from NinjaTrader ATI")

        for cb in self._disconnect_callbacks:
            try:
                cb()
            except Exception:
                logger.exception("Disconnect callback error")

    async def _send(self, command: str) -> str:
        """Send a command and read the response line."""
        if self._state != ConnectionState.CONNECTED or not self._writer or not self._reader:
            raise ConnectionError("Not connected to NinjaTrader ATI")

        self._writer.write(f"{command}\n".encode())
        await self._writer.drain()

        try:
            response = await asyncio.wait_for(
                self._reader.readline(),
                timeout=self.config.read_timeout,
            )
            return response.decode("utf-8").strip()
        except TimeoutError:
            logger.warning("ATI response timeout for command: %s", command[:50])
            return ""

    async def _listen_loop(self) -> None:
        """Background loop listening for async notifications (fills, etc.)."""
        while self._state == ConnectionState.CONNECTED and self._reader:
            try:
                line = await self._reader.readline()
                if not line:
                    # Connection closed
                    self._state = ConnectionState.DISCONNECTED
                    break
                msg = line.decode("utf-8").strip()
                if msg:
                    await self._handle_async_message(msg)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in ATI listen loop")
                await asyncio.sleep(0.1)

    async def _handle_async_message(self, msg: str) -> None:
        """Parse and dispatch async notifications from NinjaTrader.

        ATI async messages include fill notifications, order status changes,
        and position updates. Format depends on subscription type.

        TODO: Full message parsing to be refined during Phase 8 shadow trading.
        """
        logger.debug("ATI async message: %s", msg)

        # Placeholder: parse fill notifications
        # NinjaTrader ATI fill format varies; will be refined in Phase 8
        if "FILLED" in msg.upper():
            # Attempt to parse fill — structure TBD during shadow testing
            logger.info("Fill notification received: %s", msg[:100])

    async def submit_order(self, request: OrderRequest) -> OrderAck:
        """Submit an order via ATI PLACE command.

        ATI PLACE format:
            PLACE;<account>;<instrument>;<action>;<qty>;<order_type>;<limit_price>;<stop_price>;<tif>;<oco>;<order_id>;<strategy>;<strategy_id>
        """
        action = "BUY" if request.side == Side.LONG else "SELL"
        order_type_map = {
            "market": "MARKET",
            "limit": "LIMIT",
            "stop": "STOPMARKET",
        }
        nt_type = order_type_map.get(request.order_type, "MARKET")

        limit_price = str(request.price) if request.price else "0"
        stop_price = str(request.stop_price) if request.stop_price else "0"

        command = (
            f"PLACE;{self.config.account};{request.symbol};"
            f"{action};{request.qty};{nt_type};"
            f"{limit_price};{stop_price};{request.tif};;"
            f"{request.client_order_id};;"
        )

        logger.info("Submitting order: %s", command)
        response = await self._send(command)

        # Parse ATI response
        if response and not response.startswith("ERROR"):
            venue_order_id = response or f"nt-{uuid4().hex[:8]}"
            self._order_map[request.client_order_id] = venue_order_id
            return OrderAck(
                client_order_id=request.client_order_id,
                venue_order_id=venue_order_id,
                status="working",
                ts=datetime.now(tz=UTC),
            )
        return OrderAck(
            client_order_id=request.client_order_id,
            venue_order_id="",
            status="rejected",
            reject_reason=response or "No response from ATI",
            ts=datetime.now(tz=UTC),
        )

    async def cancel_order(self, client_order_id: str, venue_order_id: str) -> CancelAck:
        """Cancel an order via ATI CANCEL command."""
        command = f"CANCEL;{venue_order_id}"
        response = await self._send(command)

        success = response and not response.startswith("ERROR")
        return CancelAck(
            client_order_id=client_order_id,
            venue_order_id=venue_order_id,
            success=success,
            reason=response if not success else None,
        )

    async def cancel_all(self, symbol: str | None = None) -> list[CancelAck]:
        """Cancel all orders via ATI CANCELALLORDERS."""
        command = "CANCELALLORDERS"
        response = await self._send(command)
        # ATI returns a single ack for cancel_all
        return [
            CancelAck(
                client_order_id="*",
                venue_order_id="*",
                success=not response.startswith("ERROR") if response else True,
                reason=response if response and response.startswith("ERROR") else None,
            )
        ]

    async def get_account(self) -> AccountSnapshot:
        """Fetch account state via ATI.

        NinjaTrader ATI doesn't have a direct account query command.
        Account data is typically obtained through the ConnectionStatusEvent
        or by querying the NinjaTrader database.

        TODO: Implement via NinjaScript DLL or file-based state during Phase 7.
        """
        # Placeholder: return default snapshot
        # Will be wired to real data when NinjaTrader connection is live
        return AccountSnapshot(
            account_id=self.config.account,
            equity=Decimal("5000"),  # Edward's starting capital
            cash=Decimal("5000"),
            margin_used=Decimal("0"),
            margin_available=Decimal("5000"),
            unrealized_pnl=Decimal("0"),
            realized_pnl_today=Decimal("0"),
            ts=datetime.now(tz=UTC),
        )

    async def get_positions(self) -> list[PositionSnapshot]:
        """Fetch open positions via ATI POSITIONS command."""
        command = f"POSITIONS;{self.config.account}"
        response = await self._send(command)

        if not response or response.startswith("ERROR"):
            return []

        positions: list[PositionSnapshot] = []
        # ATI POSITIONS response format:
        # <instrument>;< qty>;< avg_price>;< unrealized_pnl>;...
        # Multiple positions separated by newlines
        for line in response.split("|"):
            parts = line.strip().split(";")
            if len(parts) >= 4:
                try:
                    positions.append(
                        PositionSnapshot(
                            symbol=parts[0],
                            qty=int(parts[1]),
                            avg_entry_price=Decimal(parts[2]),
                            unrealized_pnl=Decimal(parts[3]),
                            market_value=Decimal("0"),  # Calculated separately
                        )
                    )
                except (ValueError, IndexError):
                    logger.warning("Failed to parse position: %s", line)

        return positions

    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[QuoteTick]:
        """Stream live quotes via ATI MARKETDATA subscription.

        ATI MARKETDATA format:
            SUBSCRIBEMARKET;<instrument>;<type>
        Where type: Last, Bid, Ask
        """
        # Subscribe to all needed data types
        for symbol in symbols:
            for data_type in ("Last", "Bid", "Ask"):
                await self._send(f"SUBSCRIBEMARKET;{symbol};{data_type}")

        # Yield quotes from the listen loop
        # In practice, quotes arrive via the async listen loop
        # and are dispatched through callbacks. This is a simplified version.
        while self._state == ConnectionState.CONNECTED:
            await asyncio.sleep(0.1)
            # Real implementation: yield from an asyncio.Queue
            # populated by _handle_async_message
            # Placeholder yields nothing — to be wired in Phase 8
            return
            yield  # type: ignore[misc]  # Makes this a generator

    async def stream_bars(self, symbol: str, timeframe: str = "1m") -> AsyncIterator[BarUpdate]:
        """Stream completed bars.

        NinjaTrader ATI doesn't directly stream bars; we aggregate from
        tick data. Alternative: use NinjaScript to write bar files that
        we poll.

        TODO: Implement bar aggregation from tick stream in Phase 8.
        """
        while self._state == ConnectionState.CONNECTED:
            await asyncio.sleep(60)  # Placeholder
            return
            yield  # type: ignore[misc]

    async def heartbeat(self) -> bool:
        """Check if NinjaTrader connection is alive."""
        if self._state != ConnectionState.CONNECTED:
            return False
        try:
            # Send a lightweight command to check connectivity
            response = await self._send(f"POSITIONS;{self.config.account}")
            return response is not None
        except Exception:
            return False

    async def flatten(self, symbol: str | None = None) -> list[VenueFill]:
        """Emergency flatten via ATI CLOSEPOSITION."""
        if symbol:
            command = f"CLOSEPOSITION;{self.config.account};{symbol}"
        else:
            command = f"CLOSEPOSITION;{self.config.account};"

        await self._send(command)
        logger.warning("FLATTEN executed for %s", symbol or "ALL")
        return []  # Fills will arrive via async notification

    def on_fill(self, callback: Callable[[VenueFill], None]) -> None:
        """Register fill callback."""
        self._fill_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register disconnect callback."""
        self._disconnect_callbacks.append(callback)
