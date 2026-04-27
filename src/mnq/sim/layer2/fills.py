"""[REAL] Conservative OHLCV intrabar fill reconstruction.

For a single bar with known OHLCV, we estimate whether a stop, target,
or both would have been touched *and in what order*. The convention is
adverse-first: on any bar where both stop and target are reachable, we
assume the stop hit first. This is deliberately pessimistic — in a live
tape we don't know intrabar order from OHLCV alone.

Returns a SimulatedFill describing the exit, or None if neither the
stop nor the target was touched within the bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from mnq.core.types import Bar, Side


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    ts: datetime
    side: Side  # side of the fill (close direction)
    price: Decimal
    qty: int  # positive
    reason: str  # 'entry' | 'stop' | 'take_profit' | 'time_stop' | 'session_end'


def simulate_entry_at_open(
    bar: Bar,
    side: Side,
    qty: int,
    slippage_ticks: int,
    tick_size: Decimal,
) -> SimulatedFill:
    """Entry fills on the open of `bar`, slipped in the adverse direction."""
    slip = tick_size * slippage_ticks
    px = bar.open + slip if side is Side.LONG else bar.open - slip
    fill_side = Side.LONG if side is Side.LONG else Side.SHORT
    return SimulatedFill(
        ts=bar.ts,
        side=fill_side,
        price=px,
        qty=qty,
        reason="entry",
    )


def simulate_exit_within_bar(
    bar: Bar,
    position_side: Side,
    qty: int,
    stop_px: Decimal,
    tp_px: Decimal,
    *,
    stop_slippage_ticks: int = 0,
    limit_slippage_ticks: int = 0,
    tick_size: Decimal,
) -> SimulatedFill | None:
    """Return the exit that fires within this bar, if any (adverse-first).

    Long:
        stop triggered if bar.low <= stop_px
        target triggered if bar.high >= tp_px
        adverse-first: if both are reachable on the same bar, stop wins.
    Short: symmetric.
    """
    slip_stop = tick_size * stop_slippage_ticks
    slip_limit = tick_size * limit_slippage_ticks

    if position_side is Side.LONG:
        stop_hit = bar.low <= stop_px
        tp_hit = bar.high >= tp_px
        if stop_hit:
            px = stop_px - slip_stop
            return SimulatedFill(
                ts=bar.ts,
                side=Side.SHORT,
                price=px,
                qty=qty,
                reason="stop",
            )
        if tp_hit:
            px = tp_px - slip_limit
            return SimulatedFill(
                ts=bar.ts,
                side=Side.SHORT,
                price=px,
                qty=qty,
                reason="take_profit",
            )
        return None

    # SHORT
    stop_hit = bar.high >= stop_px
    tp_hit = bar.low <= tp_px
    if stop_hit:
        px = stop_px + slip_stop
        return SimulatedFill(
            ts=bar.ts,
            side=Side.LONG,
            price=px,
            qty=qty,
            reason="stop",
        )
    if tp_hit:
        px = tp_px + slip_limit
        return SimulatedFill(
            ts=bar.ts,
            side=Side.LONG,
            price=px,
            qty=qty,
            reason="take_profit",
        )
    return None
