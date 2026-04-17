"""Tests for contract roll handling."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import pytz  # type: ignore[import-untyped]

from mnq.core.calendar import CMEFuturesCalendar
from mnq.core.contracts import FuturesContract
from mnq.storage.journal import EventJournal
from mnq.venues.tradovate.rolls import (
    RollExecutor,
    RollPlan,
    RollScheduler,
)


@pytest.fixture
def calendar() -> CMEFuturesCalendar:
    """Create a CME calendar for testing."""
    return CMEFuturesCalendar()


@pytest.fixture
def scheduler(calendar: CMEFuturesCalendar) -> RollScheduler:
    """Create a roll scheduler."""
    return RollScheduler(
        calendar,
        early_warning_days=5,
        roll_time_before_close_min=30,
    )


def test_plan_for_mnq_march_2026(calendar: CMEFuturesCalendar) -> None:
    """Test RollPlan.plan_for for MNQ March contract.

    March 2026 roll date is 2nd Thursday before 3rd Friday.
    3rd Friday of March 2026 is March 20.
    """
    scheduler = RollScheduler(calendar)
    contract = FuturesContract.parse("MNQH26")

    # Test on March 10, 2026 (roll_date should be Mar 12)
    today = date(2026, 3, 10)
    plan = scheduler.plan_for(contract, today)

    assert plan.from_contract == contract
    assert plan.to_contract == FuturesContract.parse("MNQM26")
    assert plan.roll_date == date(2026, 3, 12)
    assert plan.days_until_roll == 2


def test_plan_for_different_months(calendar: CMEFuturesCalendar) -> None:
    """Test plan_for for different contract months."""
    scheduler = RollScheduler(calendar)

    # June contract (M)
    contract_m = FuturesContract.parse("NQM26")
    plan_m = scheduler.plan_for(contract_m, date(2026, 5, 1))
    assert plan_m.to_contract.month == 9  # Next quarter after June

    # September contract (U)
    contract_u = FuturesContract.parse("NQU26")
    plan_u = scheduler.plan_for(contract_u, date(2026, 8, 1))
    assert plan_u.to_contract.month == 12  # Next quarter after September


def test_days_until_roll_calculation(scheduler: RollScheduler) -> None:
    """Test days_until_roll is correctly calculated."""
    contract = FuturesContract.parse("MNQH26")

    # Roll date for March 2026 is March 12
    # 5 days before roll
    plan = scheduler.plan_for(contract, date(2026, 3, 7))
    assert plan.days_until_roll == 5

    # 1 day before roll
    plan = scheduler.plan_for(contract, date(2026, 3, 11))
    assert plan.days_until_roll == 1

    # On roll date
    plan = scheduler.plan_for(contract, date(2026, 3, 12))
    assert plan.days_until_roll == 0

    # After roll date
    plan = scheduler.plan_for(contract, date(2026, 3, 13))
    assert plan.days_until_roll == -1


def test_is_today_property() -> None:
    """Test RollPlan.is_today property."""
    plan = RollPlan(
        from_contract=FuturesContract.parse("MNQH26"),
        to_contract=FuturesContract.parse("MNQM26"),
        roll_date=date(2026, 3, 12),
        days_until_roll=0,
    )
    assert plan.is_today is True

    plan = RollPlan(
        from_contract=FuturesContract.parse("MNQH26"),
        to_contract=FuturesContract.parse("MNQM26"),
        roll_date=date(2026, 3, 12),
        days_until_roll=1,
    )
    assert plan.is_today is False


def test_should_warn(scheduler: RollScheduler) -> None:
    """Test should_warn returns True at T-5."""
    contract = FuturesContract.parse("MNQH26")

    # At T-5 (Mar 7)
    assert scheduler.should_warn(contract, date(2026, 3, 7)) is True

    # At T-4 (Mar 8)
    assert scheduler.should_warn(contract, date(2026, 3, 8)) is True

    # At T-6 (Mar 6) — should not warn
    assert scheduler.should_warn(contract, date(2026, 3, 6)) is False


def test_is_in_roll_window(calendar: CMEFuturesCalendar) -> None:
    """Test is_in_roll_window during RTH close window."""
    scheduler = RollScheduler(
        calendar,
        roll_time_before_close_min=30,
    )
    contract = FuturesContract.parse("MNQH26")

    # Roll date is March 12, 2026
    # RTH closes at 16:00 ET, roll window is 15:30-16:00 ET

    # Convert to UTC: 16:00 ET = 21:00 UTC (during daylight saving)
    et = pytz.timezone("America/New_York")

    # 15:35 ET on roll date (in roll window)
    roll_time_et = et.localize(datetime(2026, 3, 12, 15, 35, 0))
    roll_time_utc = roll_time_et.astimezone(UTC)
    assert scheduler.is_in_roll_window(contract, roll_time_utc) is True

    # 15:25 ET on roll date (before roll window)
    before_time_et = et.localize(datetime(2026, 3, 12, 15, 25, 0))
    before_time_utc = before_time_et.astimezone(UTC)
    assert scheduler.is_in_roll_window(contract, before_time_utc) is False

    # 16:05 ET on roll date (after roll window)
    after_time_et = et.localize(datetime(2026, 3, 12, 16, 5, 0))
    after_time_utc = after_time_et.astimezone(UTC)
    assert scheduler.is_in_roll_window(contract, after_time_utc) is False


def test_roll_executor_instructions_long_position(
    scheduler: RollScheduler,
    tmp_path: Path,
) -> None:
    """Test RollExecutor.instructions for a long position."""
    # Create journal
    journal = EventJournal(tmp_path / "test.db")
    executor = RollExecutor(scheduler, journal)

    contract = FuturesContract.parse("MNQH26")
    position = 2  # Long 2 contracts

    # Create a time during roll window (15:35 ET on March 12, 2026)
    et = pytz.timezone("America/New_York")
    roll_time_et = et.localize(datetime(2026, 3, 12, 15, 35, 0))
    roll_time_utc = roll_time_et.astimezone(UTC)

    instructions = executor.instructions(
        held_contract=contract,
        position=position,
        now=roll_time_utc,
    )

    # Should have 2 instructions: close_front + open_back
    assert len(instructions) == 2

    close_instr = instructions[0]
    assert close_instr.kind == "close_front"
    assert close_instr.contract == contract
    assert close_instr.qty == -2  # Sell to close

    open_instr = instructions[1]
    assert open_instr.kind == "open_back"
    assert open_instr.contract == FuturesContract.parse("MNQM26")
    assert open_instr.qty == 2  # Buy to open

    journal.close()


def test_roll_executor_instructions_short_position(
    scheduler: RollScheduler,
    tmp_path: Path,
) -> None:
    """Test RollExecutor.instructions for a short position."""
    journal = EventJournal(tmp_path / "test.db")
    executor = RollExecutor(scheduler, journal)

    contract = FuturesContract.parse("MNQH26")
    position = -3  # Short 3 contracts

    # Create a time during roll window
    et = pytz.timezone("America/New_York")
    roll_time_et = et.localize(datetime(2026, 3, 12, 15, 35, 0))
    roll_time_utc = roll_time_et.astimezone(UTC)

    instructions = executor.instructions(
        held_contract=contract,
        position=position,
        now=roll_time_utc,
    )

    assert len(instructions) == 2

    close_instr = instructions[0]
    assert close_instr.kind == "close_front"
    assert close_instr.qty == 3  # Buy to cover

    open_instr = instructions[1]
    assert open_instr.kind == "open_back"
    assert open_instr.qty == -3  # Sell to short

    journal.close()


def test_roll_executor_no_position(
    scheduler: RollScheduler,
    tmp_path: Path,
) -> None:
    """Test RollExecutor.instructions returns empty when position is 0."""
    journal = EventJournal(tmp_path / "test.db")
    executor = RollExecutor(scheduler, journal)

    contract = FuturesContract.parse("MNQH26")
    position = 0  # No position

    et = pytz.timezone("America/New_York")
    roll_time_et = et.localize(datetime(2026, 3, 12, 15, 35, 0))
    roll_time_utc = roll_time_et.astimezone(UTC)

    instructions = executor.instructions(
        held_contract=contract,
        position=position,
        now=roll_time_utc,
    )

    assert instructions == []

    journal.close()


def test_roll_executor_outside_roll_window(
    scheduler: RollScheduler,
    tmp_path: Path,
) -> None:
    """Test RollExecutor.instructions returns empty outside roll window."""
    journal = EventJournal(tmp_path / "test.db")
    executor = RollExecutor(scheduler, journal)

    contract = FuturesContract.parse("MNQH26")
    position = 2

    # 14:00 ET on roll date (well before roll window)
    et = pytz.timezone("America/New_York")
    time_et = et.localize(datetime(2026, 3, 12, 14, 0, 0))
    time_utc = time_et.astimezone(UTC)

    instructions = executor.instructions(
        held_contract=contract,
        position=position,
        now=time_utc,
    )

    assert instructions == []

    journal.close()


def test_roll_executor_emits_events(
    scheduler: RollScheduler,
    tmp_path: Path,
) -> None:
    """Test RollExecutor emits events for scheduled, warning, started, completed."""
    journal = EventJournal(tmp_path / "test.db")
    executor = RollExecutor(scheduler, journal)

    contract = FuturesContract.parse("MNQH26")
    position = 1

    # First call on Mar 7 (warning day)
    et = pytz.timezone("America/New_York")
    time_et = et.localize(datetime(2026, 3, 7, 10, 0, 0))
    time_utc = time_et.astimezone(UTC)

    executor.instructions(
        held_contract=contract,
        position=position,
        now=time_utc,
    )

    # Check for ROLL_SCHEDULED and ROLL_WARNING events
    entries = list(journal.replay())
    event_types = [e.event_type for e in entries]
    assert "roll.scheduled" in event_types
    assert "roll.warning" in event_types

    # Call again on roll date at roll time
    roll_time_et = et.localize(datetime(2026, 3, 12, 15, 35, 0))
    roll_time_utc = roll_time_et.astimezone(UTC)

    executor.instructions(
        held_contract=contract,
        position=position,
        now=roll_time_utc,
    )

    # Check for ROLL_STARTED and ROLL_COMPLETED
    entries = list(journal.replay())
    event_types = [e.event_type for e in entries]
    assert "roll.started" in event_types
    assert "roll.completed" in event_types

    journal.close()


def test_roll_executor_full_cycle(
    scheduler: RollScheduler,
    tmp_path: Path,
) -> None:
    """Test full roll cycle produces expected event sequence."""
    journal = EventJournal(tmp_path / "test.db")
    executor = RollExecutor(scheduler, journal)

    contract = FuturesContract.parse("MNQH26")
    position = 1

    et = pytz.timezone("America/New_York")

    # Day 1: March 7 (warning)
    time_et = et.localize(datetime(2026, 3, 7, 10, 0, 0))
    time_utc = time_et.astimezone(UTC)
    executor.instructions(
        held_contract=contract,
        position=position,
        now=time_utc,
    )

    # Day 2: March 12 (roll execution)
    roll_time_et = et.localize(datetime(2026, 3, 12, 15, 35, 0))
    roll_time_utc = roll_time_et.astimezone(UTC)
    executor.instructions(
        held_contract=contract,
        position=position,
        now=roll_time_utc,
    )

    # Verify event sequence
    entries = list(journal.replay())
    assert len(entries) >= 4

    event_types = [e.event_type for e in entries]
    # Check that all expected event types are present
    assert "roll.scheduled" in event_types
    assert "roll.warning" in event_types
    assert "roll.started" in event_types
    assert "roll.completed" in event_types

    journal.close()
