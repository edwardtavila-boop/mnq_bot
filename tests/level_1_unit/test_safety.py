"""Tests for mnq.executor.safety."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from mnq.executor.safety import CircuitBreaker, KillSwitchFile


def _now() -> datetime:
    return datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


def test_empty_breaker_allows_trade():
    cb = CircuitBreaker()
    decision = cb.allow_trade(_now())
    assert decision.allowed
    assert decision.reason == "ok"


def test_consecutive_losses_halt_trading():
    cb = CircuitBreaker(max_consecutive_losses=3)
    for _ in range(3):
        cb.record_trade(Decimal("-10"), _now())
    decision = cb.allow_trade(_now())
    assert not decision.allowed
    assert decision.reason == "consecutive_losses"


def test_winner_resets_consecutive_loss_streak():
    cb = CircuitBreaker(max_consecutive_losses=3)
    cb.record_trade(Decimal("-10"), _now())
    cb.record_trade(Decimal("-10"), _now())
    # A winner clears the streak.
    cb.record_trade(Decimal("5"), _now())
    cb.record_trade(Decimal("-10"), _now())
    decision = cb.allow_trade(_now())
    assert decision.allowed
    assert cb.consecutive_losses == 1


def test_daily_drawdown_floor_halts():
    cb = CircuitBreaker(daily_max_drawdown_usd=Decimal("-100"))
    cb.record_trade(Decimal("-60"), _now())
    cb.record_trade(Decimal("-60"), _now())
    decision = cb.allow_trade(_now())
    assert not decision.allowed
    assert decision.reason == "daily_drawdown"


def test_session_reset_clears_counters():
    cb = CircuitBreaker(max_consecutive_losses=3, daily_max_drawdown_usd=Decimal("-100"))
    for _ in range(3):
        cb.record_trade(Decimal("-50"), _now())
    # Both conditions are tripped now.
    assert not cb.allow_trade(_now()).allowed

    cb.reset_session(_now() + timedelta(days=1))
    assert cb.allow_trade(_now()).allowed
    assert cb.consecutive_losses == 0
    assert cb.session_pnl == Decimal(0)


def test_manual_halt_and_resume():
    cb = CircuitBreaker()
    cb.halt()
    assert not cb.allow_trade(_now()).allowed
    assert cb.allow_trade(_now()).reason == "manual_halt"
    cb.resume()
    assert cb.allow_trade(_now()).allowed


def test_kill_switch_blocks_trading(tmp_path: Path):
    ks_path = tmp_path / "HALT"
    ks = KillSwitchFile(path=ks_path, ttl_seconds=0.0)
    cb = CircuitBreaker(kill_switch=ks)

    assert cb.allow_trade(_now()).allowed  # file doesn't exist

    ks.arm()
    decision = cb.allow_trade(_now())
    assert not decision.allowed
    assert decision.reason == "kill_switch"

    ks.disarm()
    assert cb.allow_trade(_now()).allowed


def test_kill_switch_caching(tmp_path: Path):
    ks_path = tmp_path / "HALT"
    ks = KillSwitchFile(path=ks_path, ttl_seconds=60.0)

    t0 = _now()
    assert not ks.is_active(t0)

    # Create the file after the first check; cache should still say inactive.
    ks_path.touch()
    assert not ks.is_active(t0 + timedelta(seconds=10))

    # After TTL, re-checks.
    assert ks.is_active(t0 + timedelta(seconds=120))


def test_scratch_trade_treated_as_non_loser():
    cb = CircuitBreaker(max_consecutive_losses=2)
    cb.record_trade(Decimal("-10"), _now())
    cb.record_trade(Decimal("0"), _now())  # scratch: clears streak
    cb.record_trade(Decimal("-10"), _now())
    assert cb.allow_trade(_now()).allowed  # only 1 consecutive loss now
    assert cb.consecutive_losses == 1
    cb.record_trade(Decimal("-10"), _now())
    assert not cb.allow_trade(_now()).allowed


@pytest.mark.parametrize(
    ("pnls", "n_expected"),
    [
        ([Decimal("-10")] * 5, 5),
        ([Decimal("10")] * 5, 0),
        ([Decimal("-10"), Decimal("10"), Decimal("-10"), Decimal("-10")], 2),
    ],
)
def test_consecutive_loss_count_sequence(pnls, n_expected):
    cb = CircuitBreaker(max_consecutive_losses=999)
    for p in pnls:
        cb.record_trade(p, _now())
    assert cb.consecutive_losses == n_expected
