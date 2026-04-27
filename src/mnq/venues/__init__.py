"""Venue adapters. Each subpackage implements a single broker/exchange.

Available venues:
  - base: VenueAdapter ABC and shared types
  - ninjatrader: NinjaTrader 8 ATI adapter (Phase 6)

The executor imports only from base types. Venue selection happens at
startup via config — the executor never knows which broker it's talking to.
"""

from .base import (
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

__all__ = [
    "AccountSnapshot",
    "BarUpdate",
    "CancelAck",
    "ConnectionState",
    "OrderAck",
    "OrderRequest",
    "PositionSnapshot",
    "QuoteTick",
    "VenueAdapter",
    "VenueFill",
    "VenueType",
]
