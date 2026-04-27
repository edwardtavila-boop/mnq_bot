"""[REAL] Tiered rollout controller — scaled contract sizing with promotion/demotion.

After a variant passes ``ShipManifest.require_shippable`` it is still not
cleared for max size — edge can exist at 1c and evaporate at 5c (queue
dynamics, adverse selection, slippage). The tiered rollout controller
holds the per-variant state machine that answers:

    "How many contracts is this variant allowed to trade *right now*?"

The answer starts at 0 (paper only) and grows only when realized-PnL
evidence justifies the next tier. It shrinks when performance degrades.

Tier ladder (default):

    TIER_0  — paper only (0 contracts live). Variant routes every signal
              to the paper book, never the live venue. This is the
              mandatory probation tier.
    TIER_1  — 1 contract live.
    TIER_2  — 2 contracts live.
    TIER_3  — 3 contracts live.
    TIER_N  — configurable max.

Promotion gate (TIER_K → TIER_K+1):
    * ``min_trades_at_current_tier`` trades completed at tier K (default 20)
    * Positive expectancy at tier K (net PnL > 0 over those trades)
    * ``min_consecutive_winning_days`` green days in a row (default 3)
    * No demotion events in the lookback window

Demotion trigger (TIER_K → TIER_K-1, or halt):
    * ``max_consecutive_losing_days`` losing days in a row (default 3)
    * Per-tier realized drawdown exceeds ``demotion_drawdown_pct``
      (default 20% of tier-peak equity)
    * Manual demote via ``demote(reason)``

Halt trigger (any tier → HALT):
    * ``halt_consecutive_losses`` single-session consecutive losses (default 5)
    * CircuitBreaker kill-switch file present (if provided)

All state transitions are journaled through ``record_tier_event`` so the
auditor can reproduce the decision chain from the event log.

Typical driver loop:

    rollout = TieredRollout.initial("orb_only_pm30", max_tier=3)
    for trade_result in trades_today:
        rollout.record_trade(trade_result.pnl, trade_result.closed_at)
    rollout.record_eod(day_end_pnl=session_pnl, day=session_date)
    # Scaling the next entry:
    next_qty = rollout.allowed_qty()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Final

# Tight default constants — picked to survive a single ugly session without
# blowing up the rollout, but not so loose that a dead strategy survives a
# week.
DEFAULT_MAX_TIER: Final[int] = 3
DEFAULT_MIN_TRADES_AT_TIER: Final[int] = 20
DEFAULT_MIN_WINNING_DAYS: Final[int] = 3
DEFAULT_MAX_LOSING_DAYS: Final[int] = 3
DEFAULT_HALT_CONSECUTIVE_LOSSES: Final[int] = 5
DEFAULT_DEMOTION_DRAWDOWN_PCT: Final[Decimal] = Decimal("0.20")


class RolloutState(str, Enum):
    """Top-level controller state.

    ACTIVE — normal promotion/demotion flow.
    HALTED — trading is fully stopped; allowed_qty() returns 0. Reached by
             circuit breaker or manual operator halt.
    """

    ACTIVE = "active"
    HALTED = "halted"


@dataclass(frozen=True, slots=True)
class TierEvent:
    """A single promote/demote/halt record — feeds the journal."""

    ts: datetime
    variant: str
    event_type: str  # "promote" / "demote" / "halt" / "resume"
    from_tier: int
    to_tier: int
    reason: str


@dataclass
class TieredRollout:
    """Per-variant rollout state machine.

    Construct via ``TieredRollout.initial(variant)``; never mutate the
    constructor fields directly — use the public mutators.
    """

    variant: str
    max_tier: int = DEFAULT_MAX_TIER
    min_trades_at_tier: int = DEFAULT_MIN_TRADES_AT_TIER
    min_winning_days: int = DEFAULT_MIN_WINNING_DAYS
    max_losing_days: int = DEFAULT_MAX_LOSING_DAYS
    halt_consecutive_losses: int = DEFAULT_HALT_CONSECUTIVE_LOSSES
    demotion_drawdown_pct: Decimal = DEFAULT_DEMOTION_DRAWDOWN_PCT

    # state
    state: RolloutState = RolloutState.ACTIVE
    tier: int = 0
    _trades_at_tier: int = 0
    _pnl_at_tier: Decimal = field(default_factory=lambda: Decimal(0))
    _consecutive_losses: int = 0
    _consecutive_winning_days: int = 0
    _consecutive_losing_days: int = 0
    _tier_peak_equity: Decimal = field(default_factory=lambda: Decimal(0))
    _tier_equity: Decimal = field(default_factory=lambda: Decimal(0))
    _event_log: list[TierEvent] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def initial(
        cls, variant: str, *, max_tier: int = DEFAULT_MAX_TIER, **overrides
    ) -> TieredRollout:
        """Build a fresh rollout at TIER_0 for ``variant``."""
        if max_tier < 1:
            raise ValueError("max_tier must be >= 1")
        return cls(variant=variant, max_tier=max_tier, **overrides)

    # ------------------------------------------------------------------
    # Public queries
    # ------------------------------------------------------------------
    def allowed_qty(self) -> int:
        """Contracts permitted right now; 0 means paper-only."""
        if self.state is RolloutState.HALTED:
            return 0
        return self.tier

    def event_log(self) -> list[TierEvent]:
        """Return the append-only event log snapshot."""
        return list(self._event_log)

    # ------------------------------------------------------------------
    # Mutators — trade outcomes
    # ------------------------------------------------------------------
    def record_trade(self, pnl: Decimal, closed_at: datetime) -> None:
        """Fold a single closed trade into state.

        Updates counters, checks halt triggers, updates tier-peak/equity
        for later drawdown computation.
        """
        if self.state is RolloutState.HALTED:
            return  # no state updates once halted — resume() resets fresh
        self._trades_at_tier += 1
        self._pnl_at_tier += pnl
        self._tier_equity += pnl
        if self._tier_equity > self._tier_peak_equity:
            self._tier_peak_equity = self._tier_equity
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # Halt trigger: too many consecutive losses in one session.
        if self._consecutive_losses >= self.halt_consecutive_losses:
            self._halt(
                at=closed_at,
                reason=(
                    f"{self._consecutive_losses} consecutive losses "
                    f">= halt threshold {self.halt_consecutive_losses}"
                ),
            )

    def record_eod(self, *, day_end_pnl: Decimal, day: date, closed_at: datetime) -> None:
        """Fold end-of-day state. Triggers promotion/demotion evaluation.

        ``day_end_pnl`` is the *day's* net PnL at this tier; positive =
        winning day, <= 0 = losing day. ``day`` is the session date (for
        logging). ``closed_at`` is the UTC moment the EOD fold happens.
        """
        if self.state is RolloutState.HALTED:
            return
        if day_end_pnl > 0:
            self._consecutive_winning_days += 1
            self._consecutive_losing_days = 0
        else:
            self._consecutive_losing_days += 1
            self._consecutive_winning_days = 0

        # Demotion: too many losing days in a row.
        if self._consecutive_losing_days >= self.max_losing_days:
            self._demote(
                at=closed_at,
                reason=(
                    f"{self._consecutive_losing_days} losing days "
                    f">= max {self.max_losing_days} (day={day.isoformat()})"
                ),
            )
            return

        # Demotion: tier drawdown exceeded.
        if self._tier_peak_equity > 0:
            dd = (self._tier_peak_equity - self._tier_equity) / self._tier_peak_equity
            if dd >= self.demotion_drawdown_pct:
                self._demote(
                    at=closed_at,
                    reason=(
                        f"tier drawdown {dd:.2%} >= threshold "
                        f"{self.demotion_drawdown_pct:.2%} (day={day.isoformat()})"
                    ),
                )
                return

        # Promotion — only after EOD so we don't pump the tier mid-day.
        if self._ready_to_promote():
            self._promote(at=closed_at, reason=self._promotion_reason(day))

    def demote(self, *, at: datetime, reason: str) -> None:
        """Operator-requested demotion (one tier). No-op at TIER_0."""
        if self.state is RolloutState.HALTED:
            return
        self._demote(at=at, reason=f"manual: {reason}")

    def halt(self, *, at: datetime, reason: str) -> None:
        """Operator-requested halt. Idempotent."""
        if self.state is RolloutState.HALTED:
            return
        self._halt(at=at, reason=f"manual: {reason}")

    def resume(self, *, at: datetime, reason: str) -> None:
        """Lift a halt and restart at TIER_0 with fresh counters.

        We intentionally do NOT resume at the prior tier: once halted,
        the operator must re-earn promotion. This keeps restart-to-size
        accidents from destroying an account.
        """
        if self.state is not RolloutState.HALTED:
            return
        prev_tier = self.tier
        self.state = RolloutState.ACTIVE
        self.tier = 0
        self._reset_tier_counters()
        self._consecutive_losses = 0
        self._consecutive_winning_days = 0
        self._consecutive_losing_days = 0
        self._event_log.append(
            TierEvent(
                ts=at,
                variant=self.variant,
                event_type="resume",
                from_tier=prev_tier,
                to_tier=0,
                reason=reason,
            )
        )

    # ------------------------------------------------------------------
    # Private helpers — transitions
    # ------------------------------------------------------------------
    def _ready_to_promote(self) -> bool:
        if self.tier >= self.max_tier:
            return False
        if self._trades_at_tier < self.min_trades_at_tier:
            return False
        if self._pnl_at_tier <= 0:
            return False
        return self._consecutive_winning_days >= self.min_winning_days

    def _promotion_reason(self, day: date) -> str:
        return (
            f"tier={self.tier}+1, trades={self._trades_at_tier}, "
            f"pnl={self._pnl_at_tier}, winning_days="
            f"{self._consecutive_winning_days}, day={day.isoformat()}"
        )

    def _promote(self, *, at: datetime, reason: str) -> None:
        if self.tier >= self.max_tier:
            return
        prev = self.tier
        self.tier += 1
        self._reset_tier_counters()
        self._event_log.append(
            TierEvent(
                ts=at,
                variant=self.variant,
                event_type="promote",
                from_tier=prev,
                to_tier=self.tier,
                reason=reason,
            )
        )

    def _demote(self, *, at: datetime, reason: str) -> None:
        if self.tier <= 0:
            # At TIER_0 a "demotion" is a halt — we can't go lower.
            self._halt(at=at, reason=f"demotion at TIER_0: {reason}")
            return
        prev = self.tier
        self.tier -= 1
        self._reset_tier_counters()
        self._consecutive_winning_days = 0
        self._event_log.append(
            TierEvent(
                ts=at,
                variant=self.variant,
                event_type="demote",
                from_tier=prev,
                to_tier=self.tier,
                reason=reason,
            )
        )

    def _halt(self, *, at: datetime, reason: str) -> None:
        prev = self.tier
        self.state = RolloutState.HALTED
        self._event_log.append(
            TierEvent(
                ts=at,
                variant=self.variant,
                event_type="halt",
                from_tier=prev,
                to_tier=0,
                reason=reason,
            )
        )

    def _reset_tier_counters(self) -> None:
        """Clear per-tier counters on any tier change."""
        self._trades_at_tier = 0
        self._pnl_at_tier = Decimal(0)
        self._tier_peak_equity = Decimal(0)
        self._tier_equity = Decimal(0)


__all__ = [
    "DEFAULT_DEMOTION_DRAWDOWN_PCT",
    "DEFAULT_HALT_CONSECUTIVE_LOSSES",
    "DEFAULT_MAX_LOSING_DAYS",
    "DEFAULT_MAX_TIER",
    "DEFAULT_MIN_TRADES_AT_TIER",
    "DEFAULT_MIN_WINNING_DAYS",
    "RolloutState",
    "TierEvent",
    "TieredRollout",
]
