"""Unit tests for pre-trade risk checks and composition.

Tests verify that each risk check enforces its invariant correctly,
that composite checks short-circuit on first failure, and that
circuit breaker integration works as expected.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mnq.core.types import Side
from mnq.executor.safety import (
    CircuitBreaker,
    CompositeRiskCheck,
    FeatureStalenessCheck,
    MarginBufferCheck,
    MaxDailyLossCheck,
    MaxOpenContractsCheck,
    RiskContext,
    SessionOpeningGuard,
)
from mnq.storage.journal import EventJournal


@pytest.fixture
def tmp_journal(tmp_path: Path) -> EventJournal:
    """Fixture providing a temporary EventJournal."""
    db_path = tmp_path / "test.db"
    return EventJournal(db_path, fsync=False)


@pytest.fixture
def sample_context() -> RiskContext:
    """Fixture providing a basic RiskContext."""
    return RiskContext(
        open_positions=0,
        session_pnl=Decimal("0"),
        account_equity=Decimal("50000"),
        margin_used=Decimal("5000"),
        margin_available=Decimal("45000"),
        last_bar_ts=datetime.now(UTC),
        feature_staleness_bars={},
    )


class TestMaxOpenContractsCheck:
    """Tests for MaxOpenContractsCheck."""

    def test_allows_below_max(self, sample_context: RiskContext) -> None:
        """Allows if open position < max_contracts."""
        check = MaxOpenContractsCheck(max_contracts=2)
        context = sample_context

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert decision.allowed
        assert decision.reason == "ok"

    def test_blocks_at_max(self, sample_context: RiskContext) -> None:
        """Blocks if open position >= max_contracts."""
        check = MaxOpenContractsCheck(max_contracts=2)
        context = replace(sample_context, open_positions=2)

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed
        assert decision.reason == "max_contracts"

    def test_blocks_above_max(self, sample_context: RiskContext) -> None:
        """Blocks if open position > max_contracts."""
        check = MaxOpenContractsCheck(max_contracts=2)
        context = replace(sample_context, open_positions=3)

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed
        assert decision.reason == "max_contracts"

    def test_negative_positions_count(self, sample_context: RiskContext) -> None:
        """Open positions are checked as absolute value."""
        check = MaxOpenContractsCheck(max_contracts=1)
        context = replace(sample_context, open_positions=-2)

        decision = check.check(
            symbol="MNQ",
            side=Side.SHORT,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed


class TestMaxDailyLossCheck:
    """Tests for MaxDailyLossCheck."""

    def test_allows_positive_pnl(self, sample_context: RiskContext) -> None:
        """Allows if session P&L is positive."""
        check = MaxDailyLossCheck(max_loss_usd=Decimal("500"))
        context = replace(sample_context, session_pnl=Decimal("100"))

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert decision.allowed

    def test_allows_zero_pnl(self, sample_context: RiskContext) -> None:
        """Allows if session P&L is zero (breakeven)."""
        check = MaxDailyLossCheck(max_loss_usd=Decimal("500"))
        context = replace(sample_context, session_pnl=Decimal("0"))

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert decision.allowed

    def test_allows_small_loss(self, sample_context: RiskContext) -> None:
        """Allows if loss is less than max_loss."""
        check = MaxDailyLossCheck(max_loss_usd=Decimal("500"))
        context = replace(sample_context, session_pnl=Decimal("-200"))

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert decision.allowed

    def test_blocks_at_max_loss(self, sample_context: RiskContext) -> None:
        """Blocks if loss == max_loss."""
        check = MaxDailyLossCheck(max_loss_usd=Decimal("500"))
        context = replace(sample_context, session_pnl=Decimal("-500"))

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed
        assert decision.reason == "daily_loss"

    def test_blocks_beyond_max_loss(self, sample_context: RiskContext) -> None:
        """Blocks if loss > max_loss."""
        check = MaxDailyLossCheck(max_loss_usd=Decimal("500"))
        context = replace(sample_context, session_pnl=Decimal("-750"))

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed


class TestMarginBufferCheck:
    """Tests for MarginBufferCheck."""

    def test_allows_sufficient_margin(self, sample_context: RiskContext) -> None:
        """Allows if margin_available >= buffer + projected."""
        check = MarginBufferCheck(
            min_buffer_usd=Decimal("5000"),
            per_contract_margin_usd=Decimal("1000"),
        )
        # margin_available=45000, buffer=5000, projected=2000 (qty 2)
        context = replace(sample_context, margin_available=Decimal("45000"))

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=2,
            now=datetime.now(UTC),
            context=context,
        )

        assert decision.allowed

    def test_blocks_insufficient_margin(self, sample_context: RiskContext) -> None:
        """Blocks if margin_available < buffer + projected."""
        check = MarginBufferCheck(
            min_buffer_usd=Decimal("5000"),
            per_contract_margin_usd=Decimal("1000"),
        )
        # margin_available=6000, buffer=5000, projected=2000
        # 6000 < 5000 + 2000 = blocked
        context = replace(sample_context, margin_available=Decimal("6000"))

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=2,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed
        assert decision.reason == "margin_buffer"

    def test_blocks_at_exact_limit(self, sample_context: RiskContext) -> None:
        """Blocks if margin_available == buffer (projected is 0)."""
        check = MarginBufferCheck(
            min_buffer_usd=Decimal("45000"),
            per_contract_margin_usd=Decimal("1000"),
        )
        context = replace(sample_context, margin_available=Decimal("45000"))

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=0,
            now=datetime.now(UTC),
            context=context,
        )

        # qty=0 means no additional margin needed, but buffer check still applies
        assert decision.allowed


class TestSessionOpeningGuard:
    """Tests for SessionOpeningGuard."""

    def test_allows_without_calendar(self, sample_context: RiskContext) -> None:
        """Allows if no calendar provided."""
        check = SessionOpeningGuard(minutes=2, calendar=None)

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=sample_context,
        )

        assert decision.allowed

    def test_blocks_within_opening_period(self, sample_context: RiskContext) -> None:
        """Blocks if within N minutes of RTH open."""
        # Mock calendar
        mock_calendar = MagicMock()
        rth_open = datetime.now(UTC)
        mock_calendar.rth_window.return_value = MagicMock(
            start=rth_open,
            end=rth_open + timedelta(hours=6),
            kind="RTH",
            is_half_day=False,
        )

        check = SessionOpeningGuard(minutes=2, calendar=mock_calendar)
        # Current time is 1 minute after open
        now = rth_open + timedelta(minutes=1)

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=now,
            context=sample_context,
        )

        assert not decision.allowed
        assert decision.reason == "session_opening"

    def test_allows_after_opening_period(self, sample_context: RiskContext) -> None:
        """Allows if past N minutes after RTH open."""
        mock_calendar = MagicMock()
        rth_open = datetime.now(UTC)
        mock_calendar.rth_window.return_value = MagicMock(
            start=rth_open,
            end=rth_open + timedelta(hours=6),
            kind="RTH",
            is_half_day=False,
        )

        check = SessionOpeningGuard(minutes=2, calendar=mock_calendar)
        # Current time is 3 minutes after open
        now = rth_open + timedelta(minutes=3)

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=now,
            context=sample_context,
        )

        assert decision.allowed

    def test_allows_nontrading_day(self, sample_context: RiskContext) -> None:
        """Allows if not a trading day (rth_window returns None)."""
        mock_calendar = MagicMock()
        mock_calendar.rth_window.return_value = None

        check = SessionOpeningGuard(minutes=2, calendar=mock_calendar)

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=sample_context,
        )

        assert decision.allowed


class TestFeatureStalenessCheck:
    """Tests for FeatureStalenessCheck."""

    def test_allows_fresh_features(self, sample_context: RiskContext) -> None:
        """Allows if all critical features are fresh."""
        check = FeatureStalenessCheck(
            critical_features=("ema_fast", "ema_slow"),
            max_bars=2,
        )
        context = replace(sample_context, 
            feature_staleness_bars={"ema_fast": 0, "ema_slow": 1}
        )

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert decision.allowed

    def test_blocks_stale_feature(self, sample_context: RiskContext) -> None:
        """Blocks if any critical feature is stale (> max_bars)."""
        check = FeatureStalenessCheck(
            critical_features=("ema_fast", "ema_slow"),
            max_bars=2,
        )
        context = replace(sample_context, 
            feature_staleness_bars={"ema_fast": 0, "ema_slow": 5}
        )

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed
        assert decision.reason == "feature_staleness"

    def test_allows_missing_noncritical_features(self, sample_context: RiskContext) -> None:
        """Allows if noncritical features are missing/stale."""
        check = FeatureStalenessCheck(
            critical_features=("ema_fast",),
            max_bars=2,
        )
        context = replace(sample_context, 
            feature_staleness_bars={"ema_fast": 1, "ema_slow": 10}
        )

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert decision.allowed

    def test_blocks_missing_critical_feature(self, sample_context: RiskContext) -> None:
        """Blocks if critical feature is missing (staleness = 0 default)."""
        check = FeatureStalenessCheck(
            critical_features=("ema_fast", "ema_slow"),
            max_bars=2,
        )
        context = replace(sample_context, 
            feature_staleness_bars={"ema_fast": 1}  # ema_slow missing
        )

        decision = check.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed


class TestCompositeRiskCheck:
    """Tests for CompositeRiskCheck composition."""

    def test_all_pass_returns_allowed(self, sample_context: RiskContext) -> None:
        """All checks passing returns allowed decision."""
        check1 = MaxOpenContractsCheck(max_contracts=5)
        check2 = MaxDailyLossCheck(max_loss_usd=Decimal("500"))
        composite = CompositeRiskCheck([check1, check2])

        decision = composite.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=sample_context,
        )

        assert decision.allowed
        assert decision.reason == "ok"

    def test_first_failure_short_circuits(self, sample_context: RiskContext) -> None:
        """First check failure short-circuits, returns that decision."""
        check1 = MaxOpenContractsCheck(max_contracts=0)  # Will fail
        check2 = MaxDailyLossCheck(max_loss_usd=Decimal("-500"))  # Would fail
        composite = CompositeRiskCheck([check1, check2])

        decision = composite.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=sample_context,
        )

        assert not decision.allowed
        assert decision.reason == "max_contracts"

    def test_second_check_failure(self, sample_context: RiskContext) -> None:
        """If first passes, second failure is returned."""
        check1 = MaxOpenContractsCheck(max_contracts=5)
        check2 = MaxDailyLossCheck(max_loss_usd=Decimal("500"))
        composite = CompositeRiskCheck([check1, check2])
        context = replace(sample_context, session_pnl=Decimal("-750"))

        decision = composite.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=context,
        )

        assert not decision.allowed
        assert decision.reason == "daily_loss"

    def test_journals_each_decision(self, tmp_journal: EventJournal, sample_context: RiskContext) -> None:
        """CompositeRiskCheck logs one SAFETY_DECISION event per check."""
        check1 = MaxOpenContractsCheck(max_contracts=5)
        check2 = MaxDailyLossCheck(max_loss_usd=Decimal("500"))
        composite = CompositeRiskCheck([check1, check2], journal=tmp_journal)

        composite.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=sample_context,
        )

        # Verify 2 SAFETY_DECISION events were written
        entries = [
            e for e in tmp_journal.replay()
            if e.event_type == "safety.decision"
        ]
        assert len(entries) == 2

    def test_no_journal_still_works(self, sample_context: RiskContext) -> None:
        """CompositeRiskCheck works without a journal."""
        check1 = MaxOpenContractsCheck(max_contracts=5)
        composite = CompositeRiskCheck([check1], journal=None)

        decision = composite.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime.now(UTC),
            context=sample_context,
        )

        assert decision.allowed


class TestCircuitBreakerIntegration:
    """Tests for CircuitBreaker.allow_trade_with_checks()."""

    def test_pretrade_fail_short_circuits_breaker(self, sample_context: RiskContext) -> None:
        """Pre-trade check failure short-circuits breaker checks."""
        breaker = CircuitBreaker()
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        # Now breaker would block (5 consecutive losses)

        pretrade = CompositeRiskCheck([
            MaxOpenContractsCheck(max_contracts=0),  # Blocks immediately
        ])

        decision = breaker.allow_trade_with_checks(
            datetime.now(UTC),
            sample_context,
            pretrade,
        )

        assert not decision.allowed
        assert decision.reason == "max_contracts"

    def test_pretrade_pass_breaker_blocks(self, sample_context: RiskContext) -> None:
        """Pre-trade pass, breaker blocks."""
        breaker = CircuitBreaker()
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))
        breaker.record_trade(Decimal("-250"), datetime.now(UTC))

        pretrade = CompositeRiskCheck([
            MaxOpenContractsCheck(max_contracts=5),
        ])

        decision = breaker.allow_trade_with_checks(
            datetime.now(UTC),
            sample_context,
            pretrade,
        )

        assert not decision.allowed
        assert decision.reason == "consecutive_losses"

    def test_both_pass(self, sample_context: RiskContext) -> None:
        """Both pre-trade and breaker pass."""
        breaker = CircuitBreaker()

        pretrade = CompositeRiskCheck([
            MaxOpenContractsCheck(max_contracts=5),
            MaxDailyLossCheck(max_loss_usd=Decimal("500")),
        ])

        decision = breaker.allow_trade_with_checks(
            datetime.now(UTC),
            sample_context,
            pretrade,
        )

        assert decision.allowed
        assert decision.reason == "ok"
