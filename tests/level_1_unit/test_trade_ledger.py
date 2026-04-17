"""Tests for TradeLedger breakdown/stats helpers on `mnq.sim.layer2.engine`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from mnq.core.types import Side
from mnq.sim.layer2.engine import TradeLedger, TradeRecord


def _rec(
    pnl: str,
    *,
    side: Side = Side.LONG,
    exit_reason: str = "take_profit",
    commission: str = "1.00",
) -> TradeRecord:
    ts = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    return TradeRecord(
        entry_ts=ts,
        exit_ts=ts,
        side=side,
        qty=1,
        entry_price=Decimal("100"),
        exit_price=Decimal("100"),
        stop_price=Decimal("99"),
        take_profit_price=Decimal("102"),
        exit_reason=exit_reason,
        r_multiple=Decimal("0"),
        pnl_points=Decimal("0"),
        pnl_dollars=Decimal(pnl),
        commission_dollars=Decimal(commission),
        bars_held=3,
    )


def test_empty_ledger_breakdowns_return_empty_dicts():
    led = TradeLedger()
    assert led.breakdown_by_exit_reason() == {}
    assert led.breakdown_by_side() == {}
    assert led.win_rate() == 0.0
    assert led.expectancy_dollars() == Decimal(0)


def test_breakdown_by_exit_reason_aggregates():
    led = TradeLedger()
    led.add(_rec("10", exit_reason="take_profit"))
    led.add(_rec("-5", exit_reason="stop"))
    led.add(_rec("8", exit_reason="take_profit"))
    led.add(_rec("-3", exit_reason="time_stop"))
    bd = led.breakdown_by_exit_reason()
    assert set(bd.keys()) == {"take_profit", "stop", "time_stop"}
    assert bd["take_profit"]["n"] == 2
    assert bd["take_profit"]["pnl"] == Decimal("18")
    assert bd["take_profit"]["pnl_avg"] == Decimal("9")
    assert bd["stop"]["pnl"] == Decimal("-5")
    assert bd["time_stop"]["pnl_avg"] == Decimal("-3")


def test_breakdown_by_side_aggregates():
    led = TradeLedger()
    led.add(_rec("5", side=Side.LONG))
    led.add(_rec("-2", side=Side.LONG))
    led.add(_rec("7", side=Side.SHORT))
    bd = led.breakdown_by_side()
    assert bd["long"]["n"] == 2
    assert bd["long"]["pnl"] == Decimal("3")
    assert bd["short"]["n"] == 1
    assert bd["short"]["pnl"] == Decimal("7")


def test_win_rate_excludes_scratches_and_losers():
    led = TradeLedger()
    led.add(_rec("5"))
    led.add(_rec("-1"))
    led.add(_rec("0"))  # scratch, not a win
    led.add(_rec("3"))
    assert led.win_rate() == 0.5


def test_expectancy_is_average_net_of_commission():
    led = TradeLedger()
    led.add(_rec("10", commission="1.00"))
    led.add(_rec("-4", commission="1.00"))
    # total pnl = 6, total comm = 2, net = 4, over 2 trades -> 2
    assert led.expectancy_dollars() == Decimal("2")
