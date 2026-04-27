"""[REAL] Unit tests for mnq.risk.tiered_rollout.TieredRollout.

State-machine tests for the tiered rollout controller.

Coverage targets:
    * initial state invariants (TIER_0, ACTIVE, allowed_qty = 0)
    * promotion happy path (trades + winning days → tier up)
    * promotion *blocked* — at each of: too-few trades, negative pnl,
      not-enough-winning-days, at-max-tier
    * demotion triggers: consecutive losing days, tier drawdown, manual
    * halt triggers: consecutive losses in one session, manual, demote
      at TIER_0
    * resume — always restarts at TIER_0 with clean counters
    * event log monotonicity and content

All state is pure Python — no I/O. Fast.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from mnq.risk.tiered_rollout import (
    DEFAULT_HALT_CONSECUTIVE_LOSSES,
    DEFAULT_MAX_LOSING_DAYS,
    DEFAULT_MAX_TIER,
    DEFAULT_MIN_TRADES_AT_TIER,
    DEFAULT_MIN_WINNING_DAYS,
    RolloutState,
    TieredRollout,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_T0 = datetime(2026, 4, 1, 15, 0, tzinfo=UTC)


def _play_winning_day(
    r: TieredRollout,
    *,
    trades: int,
    win_pnl: Decimal = Decimal("50"),
    day: date = date(2026, 4, 1),
    base_ts: datetime | None = None,
) -> None:
    """Fold ``trades`` winning trades of ``win_pnl`` each, then EOD."""
    ts = base_ts or datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    for i in range(trades):
        r.record_trade(win_pnl, ts + timedelta(minutes=i))
    r.record_eod(
        day_end_pnl=win_pnl * trades,
        day=day,
        closed_at=ts + timedelta(hours=2),
    )


def _play_losing_day(
    r: TieredRollout,
    *,
    trades: int,
    loss_pnl: Decimal = Decimal("-50"),
    day: date = date(2026, 4, 1),
    base_ts: datetime | None = None,
) -> None:
    ts = base_ts or datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    for i in range(trades):
        r.record_trade(loss_pnl, ts + timedelta(minutes=i))
    r.record_eod(
        day_end_pnl=loss_pnl * trades,
        day=day,
        closed_at=ts + timedelta(hours=2),
    )


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------
class TestInitialState:
    def test_starts_at_tier_zero(self) -> None:
        r = TieredRollout.initial("v1")
        assert r.tier == 0
        assert r.state is RolloutState.ACTIVE
        assert r.allowed_qty() == 0

    def test_event_log_starts_empty(self) -> None:
        r = TieredRollout.initial("v1")
        assert r.event_log() == []

    def test_rejects_invalid_max_tier(self) -> None:
        with pytest.raises(ValueError):
            TieredRollout.initial("v1", max_tier=0)

    def test_default_constants_are_sane(self) -> None:
        assert DEFAULT_MAX_TIER >= 1
        assert DEFAULT_MIN_TRADES_AT_TIER >= 1
        assert DEFAULT_MIN_WINNING_DAYS >= 1
        assert DEFAULT_MAX_LOSING_DAYS >= 1
        assert DEFAULT_HALT_CONSECUTIVE_LOSSES >= 1


# ---------------------------------------------------------------------------
# Promotion happy path
# ---------------------------------------------------------------------------
class TestPromotionHappyPath:
    def test_promotes_after_enough_trades_and_winning_days(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=5,
            min_winning_days=2,
            max_tier=3,
        )
        # day 1: 5 winners
        _play_winning_day(r, trades=5, day=date(2026, 4, 1))
        assert r.tier == 0  # one winning day isn't enough
        # day 2: another 5 winners -> hits min_winning_days=2 AND trades=10
        _play_winning_day(r, trades=5, day=date(2026, 4, 2))
        assert r.tier == 1
        assert r.allowed_qty() == 1

    def test_promotion_resets_tier_counters(self) -> None:
        """After a promote, per-tier trade & pnl counters should reset."""
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=3,
            min_winning_days=1,
            max_tier=3,
        )
        _play_winning_day(r, trades=3, day=date(2026, 4, 1))
        assert r.tier == 1
        assert r._trades_at_tier == 0
        assert r._pnl_at_tier == Decimal(0)

    def test_can_promote_up_to_max_tier(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_tier=3,
        )
        for d in range(1, 4):
            _play_winning_day(r, trades=2, day=date(2026, 4, d))
        assert r.tier == 3
        assert r.allowed_qty() == 3

    def test_cannot_promote_beyond_max(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_tier=2,
        )
        for d in range(1, 6):
            _play_winning_day(r, trades=2, day=date(2026, 4, d))
        assert r.tier == 2  # capped


# ---------------------------------------------------------------------------
# Promotion blocked
# ---------------------------------------------------------------------------
class TestPromotionBlocked:
    def test_not_enough_trades(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=20,
            min_winning_days=1,
            max_tier=3,
        )
        _play_winning_day(r, trades=5, day=date(2026, 4, 1))
        assert r.tier == 0

    def test_negative_pnl_blocks_promotion(self) -> None:
        """Winning day count met, trade count met, but net pnl <= 0."""
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=3,
            min_winning_days=1,
            max_tier=3,
        )
        # 3 trades: big loser + 2 small winners => net < 0
        r.record_trade(Decimal("-100"), _T0)
        r.record_trade(Decimal("10"), _T0)
        r.record_trade(Decimal("10"), _T0)
        # EOD with slightly positive daily pnl so winning-day counter ticks up
        r.record_eod(
            day_end_pnl=Decimal("1"),
            day=date(2026, 4, 1),
            closed_at=_T0,
        )
        # winning-day gate met, but tier PnL is -80 — blocks
        assert r.tier == 0

    def test_not_enough_winning_days_in_a_row(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=3,
            max_tier=3,
        )
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        assert r.tier == 0
        _play_winning_day(r, trades=2, day=date(2026, 4, 2))
        assert r.tier == 0
        # 3rd winning day → promote
        _play_winning_day(r, trades=2, day=date(2026, 4, 3))
        assert r.tier == 1


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------
class TestDemotion:
    def test_consecutive_losing_days_demotes(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_losing_days=2,
            max_tier=3,
        )
        # get to tier 1
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        assert r.tier == 1
        # two losing days → demote to tier 0
        _play_losing_day(r, trades=2, day=date(2026, 4, 2))
        assert r.tier == 1  # one isn't enough
        _play_losing_day(r, trades=2, day=date(2026, 4, 3))
        assert r.tier == 0

    def test_tier_drawdown_demotes(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_losing_days=99,  # disable losing-days path
            demotion_drawdown_pct=Decimal("0.20"),
            max_tier=3,
        )
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        assert r.tier == 1
        # Accumulate 500 in equity, then draw down 150 (30%) → demote
        r.record_trade(Decimal("500"), _T0)  # equity = 500, peak = 500
        r.record_trade(Decimal("-150"), _T0)  # equity = 350, dd = 30%
        r.record_eod(
            day_end_pnl=Decimal("350"),  # still positive daily — keeps winning-day streak
            day=date(2026, 4, 2),
            closed_at=_T0,
        )
        assert r.tier == 0

    def test_manual_demote(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_tier=3,
        )
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        assert r.tier == 1
        r.demote(at=_T0, reason="operator discretion")
        assert r.tier == 0

    def test_manual_demote_at_tier_zero_becomes_halt(self) -> None:
        r = TieredRollout.initial("v1")
        assert r.tier == 0
        r.demote(at=_T0, reason="paranoia")
        assert r.state is RolloutState.HALTED
        assert r.allowed_qty() == 0


# ---------------------------------------------------------------------------
# Halt + resume
# ---------------------------------------------------------------------------
class TestHalt:
    def test_consecutive_losses_in_session_halt(self) -> None:
        r = TieredRollout.initial(
            "v1",
            halt_consecutive_losses=3,
            min_trades_at_tier=2,
            min_winning_days=1,
            max_tier=3,
        )
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        assert r.tier == 1
        for _ in range(3):
            r.record_trade(Decimal("-10"), _T0)
        assert r.state is RolloutState.HALTED
        assert r.allowed_qty() == 0

    def test_manual_halt_is_idempotent(self) -> None:
        r = TieredRollout.initial("v1")
        r.halt(at=_T0, reason="one")
        r.halt(at=_T0, reason="two")
        # Only one halt event logged
        halts = [e for e in r.event_log() if e.event_type == "halt"]
        assert len(halts) == 1

    def test_halted_ignores_trades_and_eod(self) -> None:
        r = TieredRollout.initial("v1")
        r.halt(at=_T0, reason="test")
        # Nothing moves while halted
        r.record_trade(Decimal("100"), _T0)
        r.record_eod(day_end_pnl=Decimal("100"), day=date(2026, 4, 1), closed_at=_T0)
        assert r.state is RolloutState.HALTED
        assert r.tier == 0

    def test_resume_restarts_at_tier_zero_with_clean_counters(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_tier=3,
        )
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        _play_winning_day(r, trades=2, day=date(2026, 4, 2))
        assert r.tier == 2
        r.halt(at=_T0, reason="test halt")
        r.resume(at=_T0, reason="ready again")
        assert r.state is RolloutState.ACTIVE
        assert r.tier == 0
        assert r._trades_at_tier == 0
        assert r._consecutive_winning_days == 0
        assert r._consecutive_losses == 0

    def test_resume_is_noop_when_not_halted(self) -> None:
        r = TieredRollout.initial("v1")
        r.resume(at=_T0, reason="nothing to resume")
        assert r.event_log() == []  # no resume event logged


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------
class TestEventLog:
    def test_log_is_ordered(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_losing_days=1,
            halt_consecutive_losses=99,
            max_tier=3,
        )
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        _play_losing_day(r, trades=2, day=date(2026, 4, 2))
        events = r.event_log()
        # Timestamps must be monotonic (non-decreasing)
        for a, b in zip(events, events[1:], strict=False):
            assert a.ts <= b.ts

    def test_promote_event_captures_tiers(self) -> None:
        r = TieredRollout.initial(
            "v1",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_tier=3,
        )
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        ev = [e for e in r.event_log() if e.event_type == "promote"][-1]
        assert ev.from_tier == 0
        assert ev.to_tier == 1
        assert ev.variant == "v1"

    def test_event_log_returned_as_snapshot_copy(self) -> None:
        r = TieredRollout.initial("v1")
        r.halt(at=_T0, reason="x")
        log_a = r.event_log()
        log_b = r.event_log()
        assert log_a == log_b
        log_a.clear()
        # Mutating the returned list must not touch internal state
        assert r.event_log()


# ---------------------------------------------------------------------------
# Integration: full promotion → demotion → halt cycle
# ---------------------------------------------------------------------------
class TestFullLifecycle:
    def test_promote_then_demote_then_halt_cycle(self) -> None:
        r = TieredRollout.initial(
            "orb_only_pm30",
            min_trades_at_tier=2,
            min_winning_days=1,
            max_losing_days=1,
            halt_consecutive_losses=3,
            max_tier=3,
        )
        # Promote to tier 2 over 2 winning days
        _play_winning_day(r, trades=2, day=date(2026, 4, 1))
        _play_winning_day(r, trades=2, day=date(2026, 4, 2))
        assert r.tier == 2
        # One losing day → demote to tier 1
        _play_losing_day(r, trades=2, day=date(2026, 4, 3))
        assert r.tier == 1
        # 3 consecutive intra-session losses → halt
        for _ in range(3):
            r.record_trade(Decimal("-10"), _T0)
        assert r.state is RolloutState.HALTED
        # Verify the event log tells the story coherently
        types = [e.event_type for e in r.event_log()]
        assert types == ["promote", "promote", "demote", "halt"]
