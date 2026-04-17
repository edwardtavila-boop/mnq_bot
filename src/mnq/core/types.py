"""[REAL] Core domain types and price-math helpers.

Design rules baked into this module:

1. Prices are `Decimal` at API boundaries (orders, fills, signals).
2. Features compute internally in float64 for speed, but every value that
   crosses into a comparison or order MUST be quantized to the tick grid
   via `quantize_to_tick`. Comparisons of float prices to each other or
   to Decimal prices are forbidden — use `prices_equal` or compare after
   quantization.
3. All timestamps are timezone-aware, in UTC internally. Convert to
   exchange tz only at presentation boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Final

# MNQ instrument constants. When we generalize, these become per-symbol.
MNQ_TICK_SIZE: Final[Decimal] = Decimal("0.25")
MNQ_POINT_VALUE: Final[Decimal] = Decimal("2.00")  # USD per point per contract
MNQ_TICK_VALUE: Final[Decimal] = MNQ_TICK_SIZE * MNQ_POINT_VALUE  # $0.50


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1

    @property
    def opposite(self) -> Side:
        return Side.SHORT if self is Side.LONG else Side.LONG


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    LIMIT_THEN_MARKET = "limit_then_market"  # custom: try limit, fall back


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


def now_utc() -> datetime:
    return datetime.now(UTC)


def quantize_to_tick(
    price: float | Decimal,
    tick_size: Decimal = MNQ_TICK_SIZE,
    rounding: str = ROUND_HALF_EVEN,
) -> Decimal:
    """Snap a price to the instrument's tick grid.

    Use ROUND_FLOOR for stop placement on long positions (stop must be at or
    below the float value). Use ROUND_HALF_EVEN for everything else
    (comparisons, signal prices) — banker's rounding minimizes systematic
    bias across many calls.

    >>> quantize_to_tick(18234.37) == Decimal("18234.25")
    True
    >>> quantize_to_tick(18234.13) == Decimal("18234.00")
    True
    """
    if isinstance(price, float):
        price = Decimal(repr(price))  # repr avoids 0.1 -> 0.1000...0001
    n_ticks = (price / tick_size).quantize(Decimal("1"), rounding=rounding)
    return n_ticks * tick_size


def quantize_floor(price: float | Decimal, tick: Decimal = MNQ_TICK_SIZE) -> Decimal:
    return quantize_to_tick(price, tick, rounding=ROUND_FLOOR)


def prices_equal(a: float | Decimal, b: float | Decimal, tick: Decimal = MNQ_TICK_SIZE) -> bool:
    """Two prices are equal iff they quantize to the same tick."""
    return quantize_to_tick(a, tick) == quantize_to_tick(b, tick)


def ticks_between(low: Decimal, high: Decimal, tick: Decimal = MNQ_TICK_SIZE) -> int:
    """Whole ticks between two tick-aligned prices. Errors if not aligned."""
    diff = high - low
    n = diff / tick
    if n != n.to_integral_value():
        raise ValueError(f"{low} and {high} are not both on the {tick} tick grid")
    return int(n)


def points_to_dollars(points: Decimal, qty: int, point_value: Decimal = MNQ_POINT_VALUE) -> Decimal:
    return points * Decimal(qty) * point_value


@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV bar. Timestamp is bar OPEN time, UTC."""
    ts: datetime              # bar open time, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int               # contract count
    timeframe_sec: int        # 60 for 1m

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("Bar.ts must be timezone-aware")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open {self.open} outside [low={self.low}, high={self.high}]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close {self.close} outside [low={self.low}, high={self.high}]")

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    @property
    def is_up(self) -> bool:
        return self.close >= self.open


@dataclass(frozen=True, slots=True)
class Tick:
    """Single trade tick. We don't model L2 here; that's a separate type."""
    ts: datetime               # tick time, UTC, with millisecond precision min
    price: Decimal
    size: int
    aggressor: Side | None     # None if not classified


@dataclass(frozen=True, slots=True)
class Signal:
    """Output of a strategy's `on_bar()`. Consumed by the order manager."""
    side: Side
    qty: int
    ref_price: Decimal         # signal price (typically bar close)
    stop: Decimal              # protective stop, tick-aligned
    take_profit: Decimal       # target, tick-aligned
    order_type: OrderType
    limit_offset_ticks: int = 0
    market_fallback_ms: int = 500
    time_stop_bars: int = 20
    breakeven_at_r: Decimal | None = None
    trail: dict[str, object] | None = None
    spec_hash: str = ""
    spec_semver: str = ""

    def __post_init__(self) -> None:
        # Guard: stop and target must be on the correct sides
        if self.side is Side.LONG:
            if not (self.stop < self.ref_price < self.take_profit):
                raise ValueError(
                    f"long signal: need stop({self.stop}) < ref({self.ref_price}) "
                    f"< tp({self.take_profit})"
                )
        else:
            if not (self.take_profit < self.ref_price < self.stop):
                raise ValueError(
                    f"short signal: need tp({self.take_profit}) < ref({self.ref_price}) "
                    f"< stop({self.stop})"
                )

    @property
    def stop_distance_pts(self) -> Decimal:
        return abs(self.ref_price - self.stop)

    @property
    def reward_to_risk(self) -> Decimal:
        return abs(self.take_profit - self.ref_price) / self.stop_distance_pts


@dataclass(frozen=True, slots=True)
class Fill:
    """A confirmed fill from the venue."""
    order_id: str
    spec_hash: str
    ts: datetime
    side: Side
    qty: int
    price: Decimal
    commission: Decimal
    venue: str                 # 'tradovate_paper' | 'tradovate_live' | 'mock'
    venue_fill_id: str
    is_partial: bool = False


@dataclass(frozen=True, slots=True)
class Position:
    """Net position in one instrument."""
    symbol: str
    qty: int                   # signed: positive long, negative short, 0 flat
    avg_price: Decimal         # 0 if flat
    realized_pnl_session: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)

    @property
    def is_flat(self) -> bool:
        return self.qty == 0

    @property
    def side(self) -> Side | None:
        if self.qty > 0:
            return Side.LONG
        if self.qty < 0:
            return Side.SHORT
        return None
