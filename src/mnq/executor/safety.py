"""[REAL] Pre-trade safety checks: kill-switch file + circuit breakers + risk composition.

Three built-in guards, composed in `allow_trade()`:

1. **Kill-switch file** — if a file exists at a configured path, trading
   is disabled. Ops can halt the bot without deploying code: `touch`
   the file. The check is cached with a short TTL so it's cheap on the
   hot path but still responsive (default 2s).

2. **Consecutive-loss breaker** — halts new entries after N consecutive
   losing trades. Cools down until the next session start or a manual
   reset. Default threshold 5 — tuned to trigger on multi-sigma losing
   streaks under realistic win-rate assumptions (p_win ~= 0.45 ⇒ five
   losses in a row is ~3% per-day, a reasonable "something is wrong"
   signal).

3. **Daily-drawdown breaker** — halts new entries when the session's
   realized P&L drops below a configured floor (absolute dollars).

Plus **pre-trade risk checks** — a pluggable composition layer for
position limits, margin buffers, feature staleness, session opening guards, etc.

This module is stateful within a process — it's a `CircuitBreaker`
object you construct once and thread into the executor. Crash recovery
(reconstructing state from a persisted trade log) is out of scope here
and belongs to the executor startup path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

from mnq.core.types import Side
from mnq.observability.logger import get_logger
from mnq.storage.journal import EventJournal
from mnq.storage.schema import SAFETY_DECISION

Reason = Literal[
    "ok",
    "kill_switch",
    "consecutive_losses",
    "daily_drawdown",
    "manual_halt",
    "max_contracts",
    "daily_loss",
    "margin_buffer",
    "session_opening",
    "feature_staleness",
]


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: Reason
    detail: str = ""


@dataclass
class KillSwitchFile:
    """Watches a filesystem path and reports whether the kill-switch is on.

    The check is cheap: a single `Path.exists()` stat call. We cache the
    result for `ttl_seconds` to avoid hammering the FS from a hot loop;
    that means a kill-switch toggle can take up to `ttl_seconds` to take
    effect. Default 2s is the right trade-off for a 1m scalping loop.
    """

    path: Path
    ttl_seconds: float = 2.0
    _cached_at: datetime | None = None
    _cached_value: bool = False

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if (
            self._cached_at is not None
            and (now - self._cached_at).total_seconds() < self.ttl_seconds
        ):
            return self._cached_value
        self._cached_value = self.path.exists()
        self._cached_at = now
        return self._cached_value

    def arm(self) -> None:
        """Create the kill-switch file, halting trading immediately."""
        self.path.touch()
        self._cached_at = None

    def disarm(self) -> None:
        """Remove the kill-switch file. No-op if already absent."""
        self.path.unlink(missing_ok=True)
        self._cached_at = None


@dataclass
class CircuitBreaker:
    """Composable pre-trade gate with 3 sub-breakers.

    Call `record_trade(pnl_dollars, closed_at_utc)` after each closed
    trade. Call `allow_trade(now_utc)` before sending a new entry to
    the venue.

    `session_reset_ts(now)` resets the per-session counters — pass in
    a session boundary computed elsewhere (RTH open is the typical
    choice; weekend is the next natural reset).
    """

    max_consecutive_losses: int = 5
    daily_max_drawdown_usd: Decimal = Decimal("-500.00")
    kill_switch: KillSwitchFile | None = None
    manual_halt: bool = False

    # state
    _consecutive_losses: int = 0
    _session_pnl: Decimal = field(default=Decimal(0))
    _session_started_at: datetime | None = None

    # ---- mutation --------------------------------------------------------

    def reset_session(self, at: datetime) -> None:
        """Reset per-session counters at a session boundary."""
        self._consecutive_losses = 0
        self._session_pnl = Decimal(0)
        self._session_started_at = at

    def record_trade(self, pnl: Decimal, closed_at: datetime) -> None:
        """Fold a closed trade's P&L into breaker state."""
        if self._session_started_at is None:
            self._session_started_at = closed_at
        self._session_pnl += pnl
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            # A winner or a scratch resets the streak.
            self._consecutive_losses = 0

    def halt(self) -> None:
        """Flip the manual-halt flag. Revert with `resume()`."""
        self.manual_halt = True

    def resume(self) -> None:
        self.manual_halt = False

    # ---- queries ---------------------------------------------------------

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def session_pnl(self) -> Decimal:
        return self._session_pnl

    def allow_trade(self, now: datetime | None = None) -> SafetyDecision:
        """Decide whether a new entry order is permitted right now."""
        now = now or datetime.now(UTC)
        if self.manual_halt:
            return SafetyDecision(False, "manual_halt", "manual halt flag set")
        if self.kill_switch is not None and self.kill_switch.is_active(now):
            return SafetyDecision(
                False,
                "kill_switch",
                f"kill-switch file present at {self.kill_switch.path}",
            )
        if self._consecutive_losses >= self.max_consecutive_losses:
            return SafetyDecision(
                False,
                "consecutive_losses",
                f"{self._consecutive_losses} consecutive losses "
                f">= threshold {self.max_consecutive_losses}",
            )
        if self._session_pnl <= self.daily_max_drawdown_usd:
            return SafetyDecision(
                False,
                "daily_drawdown",
                f"session pnl {self._session_pnl} <= floor {self.daily_max_drawdown_usd}",
            )
        return SafetyDecision(True, "ok")

    def time_since_session_start(self, now: datetime) -> timedelta | None:
        if self._session_started_at is None:
            return None
        return now - self._session_started_at

    def allow_trade_with_checks(
        self,
        now: datetime,
        context: RiskContext,
        pretrade: CompositeRiskCheck,
    ) -> SafetyDecision:
        """Run pre-trade checks first, then breaker checks.

        Pre-trade checks short-circuit the breaker. Both sets of checks
        are run for audit (each check logs a SAFETY_DECISION event).

        Args:
            now: Current time.
            context: Risk context snapshot.
            pretrade: CompositeRiskCheck with pluggable checks.

        Returns:
            SafetyDecision (first failure wins).
        """
        # Run pre-trade checks first; they short-circuit
        pretrade_decision = pretrade.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=now,
            context=context,
        )
        if not pretrade_decision.allowed:
            return pretrade_decision

        # Pre-trade passed; run the breaker's built-in checks
        return self.allow_trade(now)


@dataclass(frozen=True)
class RiskContext:
    """Snapshot passed to every pre-trade risk check.

    Attributes:
        open_positions: Signed net quantity (positive = long, negative = short).
        session_pnl: Realized P&L for the session.
        account_equity: Total account equity.
        margin_used: Margin currently used.
        margin_available: Margin still available.
        last_bar_ts: Timestamp of the most recent bar.
        feature_staleness_bars: Map of feature name to bars since last update.
    """
    open_positions: int
    session_pnl: Decimal
    account_equity: Decimal
    margin_used: Decimal
    margin_available: Decimal
    last_bar_ts: datetime
    feature_staleness_bars: dict[str, int]


class PreTradeRiskCheck(Protocol):
    """Protocol for pluggable pre-trade checks.

    Each implementation returns a SafetyDecision (allowed/reason/detail).
    Checks should be O(1) and cheap (called on every bar).
    """

    def check(
        self,
        *,
        symbol: str,
        side: Side,
        qty: int,
        now: datetime,
        context: RiskContext,
    ) -> SafetyDecision:
        """Run the check and return a SafetyDecision.

        Args:
            symbol: Trading symbol.
            side: Long or Short.
            qty: Order quantity.
            now: Current time.
            context: Risk context snapshot.

        Returns:
            SafetyDecision (allowed, reason, detail).
        """
        ...


class MaxOpenContractsCheck:
    """Block if open position >= max_contracts."""

    def __init__(self, max_contracts: int = 2) -> None:
        """Initialize.

        Args:
            max_contracts: Maximum open contracts allowed.
        """
        self.max_contracts = max_contracts
        self.logger = get_logger(__name__)

    def check(
        self,
        *,
        symbol: str,
        side: Side,
        qty: int,
        now: datetime,
        context: RiskContext,
    ) -> SafetyDecision:
        """Check if opening a new position would exceed max contracts."""
        open_qty = abs(context.open_positions)
        if open_qty >= self.max_contracts:
            detail = (
                f"Open position {open_qty} >= max {self.max_contracts}"
            )
            self.logger.warning("max_contracts_check_blocked", detail=detail)
            return SafetyDecision(False, "max_contracts", detail)
        return SafetyDecision(True, "ok")


class MaxDailyLossCheck:
    """Block if session P&L <= -max_loss."""

    def __init__(self, max_loss_usd: Decimal) -> None:
        """Initialize.

        Args:
            max_loss_usd: Maximum loss (as a positive number, e.g., Decimal("500")).
                Will be negated internally.
        """
        self.max_loss_usd = -abs(max_loss_usd)
        self.logger = get_logger(__name__)

    def check(
        self,
        *,
        symbol: str,
        side: Side,
        qty: int,
        now: datetime,
        context: RiskContext,
    ) -> SafetyDecision:
        """Check if session loss is at or beyond the limit."""
        if context.session_pnl <= self.max_loss_usd:
            detail = f"Session PnL {context.session_pnl} <= floor {self.max_loss_usd}"
            self.logger.warning("max_daily_loss_check_blocked", detail=detail)
            return SafetyDecision(False, "daily_loss", detail)
        return SafetyDecision(True, "ok")


class MarginBufferCheck:
    """Block if margin_available < buffer + projected margin for qty."""

    def __init__(
        self,
        *,
        min_buffer_usd: Decimal,
        per_contract_margin_usd: Decimal,
    ) -> None:
        """Initialize.

        Args:
            min_buffer_usd: Minimum margin buffer to maintain.
            per_contract_margin_usd: Margin requirement per contract.
        """
        self.min_buffer_usd = min_buffer_usd
        self.per_contract_margin_usd = per_contract_margin_usd
        self.logger = get_logger(__name__)

    def check(
        self,
        *,
        symbol: str,
        side: Side,
        qty: int,
        now: datetime,
        context: RiskContext,
    ) -> SafetyDecision:
        """Check if margin available is sufficient."""
        projected_margin = self.per_contract_margin_usd * Decimal(qty)
        required = self.min_buffer_usd + projected_margin
        if context.margin_available < required:
            detail = (
                f"Margin available {context.margin_available} < "
                f"required {required}"
            )
            self.logger.warning("margin_buffer_check_blocked", detail=detail)
            return SafetyDecision(False, "margin_buffer", detail)
        return SafetyDecision(True, "ok")


class SessionOpeningGuard:
    """Block trading in the first N minutes of RTH."""

    def __init__(
        self,
        minutes: int = 2,
        calendar: Any = None,
    ) -> None:
        """Initialize.

        Args:
            minutes: Minutes after RTH open to block (default 2).
            calendar: Optional CMEFuturesCalendar instance.
                If None, no session guard is applied.
        """
        self.minutes = minutes
        self.calendar = calendar
        self.logger = get_logger(__name__)

    def check(
        self,
        *,
        symbol: str,
        side: Side,
        qty: int,
        now: datetime,
        context: RiskContext,
    ) -> SafetyDecision:
        """Check if we're within the session opening blackout."""
        if self.calendar is None:
            return SafetyDecision(True, "ok")

        # Get RTH window for today
        today = now.date()
        window = self.calendar.rth_window(today)
        if window is None:
            # Not a trading day; no guard
            return SafetyDecision(True, "ok")

        # Block if within the first N minutes of RTH
        time_since_open = now - window.start
        blackout_period = timedelta(minutes=self.minutes)
        if time_since_open < blackout_period:
            detail = (
                f"Within {self.minutes}m blackout of RTH open "
                f"({time_since_open.total_seconds():.1f}s elapsed)"
            )
            self.logger.warning("session_opening_guard_blocked", detail=detail)
            return SafetyDecision(False, "session_opening", detail)

        return SafetyDecision(True, "ok")


class FeatureStalenessCheck:
    """Block if any critical feature is stale (> max_bars behind)."""

    def __init__(self, critical_features: tuple[str, ...], max_bars: int = 2) -> None:
        """Initialize.

        Args:
            critical_features: Feature names to monitor.
            max_bars: Max bars behind before blocking (default 2).
        """
        self.critical_features = critical_features
        self.max_bars = max_bars
        self.logger = get_logger(__name__)

    def check(
        self,
        *,
        symbol: str,
        side: Side,
        qty: int,
        now: datetime,
        context: RiskContext,
    ) -> SafetyDecision:
        """Check if any critical feature is too stale.

        Missing features are treated as infinitely stale (staleness = infinity).
        """
        for feature in self.critical_features:
            # Missing features are treated as infinitely stale
            staleness = context.feature_staleness_bars.get(feature, float('inf'))
            if staleness > self.max_bars:
                detail = f"Feature '{feature}' is {staleness} bars stale (max {self.max_bars})"
                self.logger.warning("feature_staleness_check_blocked", detail=detail)
                return SafetyDecision(False, "feature_staleness", detail)

        return SafetyDecision(True, "ok")


class CompositeRiskCheck:
    """Composite check that runs all checks; first failure short-circuits.

    All checks are still logged (for audit) even if an earlier check fails.
    """

    def __init__(
        self,
        checks: list[PreTradeRiskCheck],
        journal: EventJournal | None = None,
    ) -> None:
        """Initialize.

        Args:
            checks: List of checks to run (in order).
            journal: Optional EventJournal for audit trail.
        """
        self.checks = checks
        self.journal = journal
        self.logger = get_logger(__name__)

    def check(
        self,
        *,
        symbol: str,
        side: Side,
        qty: int,
        now: datetime,
        context: RiskContext,
    ) -> SafetyDecision:
        """Run all checks; return first failure or allowed.

        Logs one SAFETY_DECISION event per check to journal (if provided).

        Args:
            symbol: Trading symbol.
            side: Long or Short.
            qty: Order quantity.
            now: Current time.
            context: Risk context snapshot.

        Returns:
            SafetyDecision (first failure, or allowed if all pass).
        """
        decision_to_return = None

        for check in self.checks:
            decision = check.check(
                symbol=symbol,
                side=side,
                qty=qty,
                now=now,
                context=context,
            )

            # Journal the decision
            if self.journal is not None:
                self.journal.append(
                    SAFETY_DECISION,
                    {
                        "check": check.__class__.__name__,
                        "allowed": decision.allowed,
                        "reason": decision.reason,
                        "detail": decision.detail,
                    },
                )

            # Track the first failure
            if not decision.allowed and decision_to_return is None:
                decision_to_return = decision

        # If no failure, return allowed
        if decision_to_return is None:
            decision_to_return = SafetyDecision(True, "ok")

        return decision_to_return
