"""[IMPL] Contract roll handling for MNQ/NQ/ES/MES futures.

Implements roll scheduling, status tracking, and execution coordination
following CME quarterly roll conventions:
  - Roll to next quarterly contract on 2nd Thursday before 3rd Friday
  - Execution during RTH, ~30 min before close, to minimize basis exposure
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from mnq.storage.journal import EventJournal

if TYPE_CHECKING:
    from mnq.core.calendar import CMEFuturesCalendar
    from mnq.core.contracts import FuturesContract


@dataclass(frozen=True)
class RollPlan:
    """Describes the scheduled roll from one contract to the next."""

    from_contract: FuturesContract
    to_contract: FuturesContract
    roll_date: date
    days_until_roll: int

    @property
    def is_today(self) -> bool:
        """Check if roll_date is today."""
        return self.days_until_roll == 0


@dataclass(frozen=True)
class RollStatus:
    """Live status during a roll."""

    plan: RollPlan
    from_position_closed: bool
    to_position_opened: bool
    basis_ticks: float
    completed: bool


@dataclass(frozen=True)
class RollInstruction:
    """Instruction to execute during a roll."""

    kind: str  # "close_front" | "open_back"
    contract: FuturesContract
    qty: int  # signed: negative = sell to close long, positive = buy back
    reason: str


class RollScheduler:
    """Decides when and how to roll a futures position.

    The MNQ/NQ convention we follow: roll to the next quarterly contract
    on the roll date (2nd Thursday before 3rd Friday of the front-month).
    Actual roll execution happens during RTH, ~30 min before close, to
    minimize basis exposure.
    """

    def __init__(
        self,
        calendar: CMEFuturesCalendar,
        *,
        early_warning_days: int = 5,
        roll_time_before_close_min: int = 30,
    ) -> None:
        """Initialize the roll scheduler.

        Args:
            calendar: CMEFuturesCalendar instance.
            early_warning_days: Days before roll_date to trigger warning.
            roll_time_before_close_min: Minutes before RTH close to execute roll.
        """
        self.calendar = calendar
        self.early_warning_days = early_warning_days
        self.roll_time_before_close_min = roll_time_before_close_min

    def plan_for(self, contract: FuturesContract, today: date) -> RollPlan:
        """Return the current RollPlan assuming `contract` is the
        currently-held position.

        Args:
            contract: The currently-held contract.
            today: Reference date for days_until_roll calculation.

        Returns:
            RollPlan with roll_date and days_until_roll.
        """
        roll_date = self.calendar.quarterly_roll_date(contract.symbol(), contract.year)
        days_until = (roll_date - today).days
        next_contract = contract.next_contract()

        return RollPlan(
            from_contract=contract,
            to_contract=next_contract,
            roll_date=roll_date,
            days_until_roll=days_until,
        )

    def is_in_roll_window(self, contract: FuturesContract, now: datetime) -> bool:
        """True during the roll_time window on the roll_date.

        The roll window is (RTH_close - roll_time_before_close_min) to RTH_close
        on the roll_date.

        Args:
            contract: The contract being rolled.
            now: Current time (UTC).

        Returns:
            True if now is within the roll window.
        """
        # Get roll date for the contract
        roll_date = self.calendar.quarterly_roll_date(contract.symbol(), contract.year)

        # Get RTH window for roll_date
        rth = self.calendar.rth_window(roll_date)
        if rth is None:
            return False

        # Roll window: 30 min before close to close
        roll_start = rth.end - timedelta(minutes=self.roll_time_before_close_min)
        roll_end = rth.end

        # Normalize now to UTC
        now_utc = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

        return roll_start <= now_utc <= roll_end

    def should_warn(self, contract: FuturesContract, today: date) -> bool:
        """True if days_until_roll <= early_warning_days.

        Args:
            contract: The contract being held.
            today: Reference date.

        Returns:
            True if warning threshold reached.
        """
        plan = self.plan_for(contract, today)
        return plan.days_until_roll <= self.early_warning_days


class RollExecutor:
    """Coordinates the actual roll: close old, open new, emit events.

    Intended to be called by the executor on each bar; it checks whether
    we're in the roll window and returns a list of instructions to execute.
    """

    def __init__(self, scheduler: RollScheduler, journal: EventJournal | None = None) -> None:
        """Initialize the roll executor.

        Args:
            scheduler: RollScheduler instance.
            journal: Optional EventJournal for logging roll events.
        """
        self.scheduler = scheduler
        self.journal = journal
        self._last_plan_date: date | None = None
        self._last_warning_date: date | None = None

    def instructions(
        self,
        *,
        held_contract: FuturesContract,
        position: int,
        now: datetime,
    ) -> list[RollInstruction]:
        """Return roll instructions to execute on this bar, or [] if no
        action needed.

        Args:
            held_contract: The currently-held contract.
            position: Signed quantity (positive=long, negative=short).
            now: Current datetime (UTC).

        Returns:
            List of RollInstruction objects to execute (empty if no action).
        """
        # Get today's date (from now, normalized to trading calendar)
        now_utc = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

        import pytz  # type: ignore[import-untyped]

        et = pytz.timezone("America/New_York")
        now_et = now_utc.astimezone(et)
        today = now_et.date()

        # Get the plan for this contract
        plan = self.scheduler.plan_for(held_contract, today)

        # Log scheduled roll on first planning (once per day)
        if self._last_plan_date != today:
            self._last_plan_date = today
            if self.journal:
                self.journal.append(
                    "roll.scheduled",
                    {
                        "from_contract": held_contract.symbol(),
                        "to_contract": plan.to_contract.symbol(),
                        "roll_date": plan.roll_date.isoformat(),
                        "days_until_roll": plan.days_until_roll,
                    },
                )

        # Log warning on first warning day
        if self.scheduler.should_warn(held_contract, today) and self._last_warning_date != today:
            self._last_warning_date = today
            if self.journal:
                self.journal.append(
                    "roll.warning",
                    {
                        "from_contract": held_contract.symbol(),
                        "days_until_roll": plan.days_until_roll,
                    },
                )

        # If no position, nothing to roll
        if position == 0:
            return []

        # Check if we're in the roll window
        if not self.scheduler.is_in_roll_window(held_contract, now_utc):
            return []

        # Generate instructions: close old, open new
        instructions: list[RollInstruction] = []

        # Log roll start
        if self.journal:
            self.journal.append(
                "roll.started",
                {
                    "from_contract": held_contract.symbol(),
                    "to_contract": plan.to_contract.symbol(),
                    "position": position,
                },
            )

        # Close the front contract
        close_qty = -position  # Opposite sign to close
        instructions.append(
            RollInstruction(
                kind="close_front",
                contract=held_contract,
                qty=close_qty,
                reason="Roll from expiring contract",
            )
        )

        # Open the back contract
        open_qty = position  # Same sign as original position
        instructions.append(
            RollInstruction(
                kind="open_back",
                contract=plan.to_contract,
                qty=open_qty,
                reason="Roll to next quarterly contract",
            )
        )

        # Log roll completion
        if self.journal:
            self.journal.append(
                "roll.completed",
                {
                    "from_contract": held_contract.symbol(),
                    "to_contract": plan.to_contract.symbol(),
                    "position": position,
                },
            )

        return instructions
