"""Level-1 unit tests for mnq.calibration.recorder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from mnq.calibration.recorder import (
    ExpectedFillContext,
    SlippageRecorder,
    export_to_dataframe,
)
from mnq.core.types import Side
from mnq.storage.journal import EventJournal
from mnq.storage.schema import FILL_EXPECTED, FILL_ORPHANED, FILL_REALIZED


@pytest.fixture
def temp_journal_path() -> Path:
    """Create a temporary journal for testing.

    Uses ``ignore_cleanup_errors=True`` because tests open ``EventJournal``
    against this path but don't always close the underlying SQLite
    connection -- on Windows the WAL/SHM files stay locked and the
    ``TemporaryDirectory.__exit__`` raises ``PermissionError`` even though
    the test itself passed. Ignoring cleanup errors avoids that false
    failure without changing what we're actually asserting.
    """
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Path(tmp) / "test.db"


@pytest.fixture
def sample_expected_ctx() -> ExpectedFillContext:
    """Create a sample expected fill context."""
    return ExpectedFillContext(
        order_id="test_order_1",
        symbol="MNQ",
        side=Side.LONG,
        qty=100,
        submitted_at=datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC),
        expected_price=Decimal("18234.00"),
        reference_bid=Decimal("18233.75"),
        reference_ask=Decimal("18234.25"),
        spread_ticks=2.0,
        volatility_regime="normal",
        tod_bucket="rth_body",
        liquidity_proxy=5000.0,
        tick_size=Decimal("0.25"),
    )


class TestExpectedFillContext:
    def test_can_create_context(self, sample_expected_ctx: ExpectedFillContext) -> None:
        assert sample_expected_ctx.order_id == "test_order_1"
        assert sample_expected_ctx.side == Side.LONG
        assert sample_expected_ctx.qty == 100


class TestSlippageRecorder:
    def test_record_expected_stores_in_memory(
        self, sample_expected_ctx: ExpectedFillContext
    ) -> None:
        rec = SlippageRecorder()
        assert rec.pending_count() == 0
        rec.record_expected(sample_expected_ctx)
        assert rec.pending_count() == 1

    def test_record_realized_happy_path_long(
        self, sample_expected_ctx: ExpectedFillContext
    ) -> None:
        rec = SlippageRecorder()
        rec.record_expected(sample_expected_ctx)

        result = rec.record_realized(
            order_id="test_order_1",
            realized_price=Decimal("18234.25"),  # worse by 1 tick
            realized_at=datetime(2026, 4, 14, 10, 0, 1, tzinfo=UTC),
            fill_qty=100,
        )

        assert result is not None
        assert result.order_id == "test_order_1"
        assert result.realized_price == Decimal("18234.25")
        assert result.fill_qty == 100
        # 1 tick worse: (18234.25 - 18234.00) / 0.25 = 1.0
        assert result.slippage_ticks == pytest.approx(1.0)
        # latency_ms should be ~1000 ms (1 second)
        assert result.latency_ms == pytest.approx(1000.0, rel=0.01)

    def test_record_realized_happy_path_short(self) -> None:
        ctx = ExpectedFillContext(
            order_id="test_short",
            symbol="MNQ",
            side=Side.SHORT,
            qty=100,
            submitted_at=datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC),
            expected_price=Decimal("18234.00"),
            reference_bid=Decimal("18233.75"),
            reference_ask=Decimal("18234.25"),
            spread_ticks=2.0,
            volatility_regime="normal",
            tod_bucket="rth_body",
            liquidity_proxy=5000.0,
            tick_size=Decimal("0.25"),
        )
        rec = SlippageRecorder()
        rec.record_expected(ctx)

        result = rec.record_realized(
            order_id="test_short",
            realized_price=Decimal("18233.75"),  # worse by 1 tick
            realized_at=datetime(2026, 4, 14, 10, 0, 1, tzinfo=UTC),
            fill_qty=100,
        )

        assert result is not None
        # For SHORT: expected < realized is worse: (18234.00 - 18233.75) / 0.25 = 1.0
        assert result.slippage_ticks == pytest.approx(1.0)

    def test_slippage_ticks_sign_long(self, sample_expected_ctx: ExpectedFillContext) -> None:
        rec = SlippageRecorder()
        rec.record_expected(sample_expected_ctx)

        # Better fill
        result_better = rec.record_realized(
            order_id="test_order_1",
            realized_price=Decimal("18233.75"),  # better by 1 tick
            realized_at=datetime(2026, 4, 14, 10, 0, 1, tzinfo=UTC),
            fill_qty=100,
        )
        # (18233.75 - 18234.00) / 0.25 = -1.0
        assert result_better is not None
        assert result_better.slippage_ticks == pytest.approx(-1.0)

    def test_unknown_order_id_returns_none(self) -> None:
        rec = SlippageRecorder()
        result = rec.record_realized(
            order_id="unknown",
            realized_price=Decimal("18234.00"),
            realized_at=datetime.now(UTC),
            fill_qty=100,
        )
        assert result is None

    def test_drop_expired_removes_old_expectations(
        self, sample_expected_ctx: ExpectedFillContext
    ) -> None:
        rec = SlippageRecorder(timeout_s=10.0)
        rec.record_expected(sample_expected_ctx)
        assert rec.pending_count() == 1

        # Time has not advanced; not expired
        now = sample_expected_ctx.submitted_at + timedelta(seconds=5)
        dropped = rec.drop_expired(now)
        assert len(dropped) == 0
        assert rec.pending_count() == 1

        # Now advance past timeout
        now = sample_expected_ctx.submitted_at + timedelta(seconds=15)
        dropped = rec.drop_expired(now)
        assert "test_order_1" in dropped
        assert rec.pending_count() == 0

    def test_pending_count_tracks_multiple(self, sample_expected_ctx: ExpectedFillContext) -> None:
        rec = SlippageRecorder()
        rec.record_expected(sample_expected_ctx)
        assert rec.pending_count() == 1

        ctx2 = ExpectedFillContext(
            order_id="test_order_2",
            symbol="MNQ",
            side=Side.SHORT,
            qty=50,
            submitted_at=datetime(2026, 4, 14, 10, 0, 30, tzinfo=UTC),
            expected_price=Decimal("18235.00"),
            reference_bid=Decimal("18234.75"),
            reference_ask=Decimal("18235.25"),
            spread_ticks=2.0,
            volatility_regime="normal",
            tod_bucket="rth_body",
            liquidity_proxy=5000.0,
            tick_size=Decimal("0.25"),
        )
        rec.record_expected(ctx2)
        assert rec.pending_count() == 2

    def test_journal_emits_fill_expected_event(
        self, sample_expected_ctx: ExpectedFillContext, temp_journal_path: Path
    ) -> None:
        journal = EventJournal(temp_journal_path)
        rec = SlippageRecorder(journal=journal)
        rec.record_expected(sample_expected_ctx)

        # Replay and verify
        events = list(journal.replay(event_types=(FILL_EXPECTED,)))
        assert len(events) == 1
        assert events[0].event_type == FILL_EXPECTED
        assert events[0].payload["order_id"] == "test_order_1"
        assert events[0].trace_id == "test_order_1"
        assert events[0].payload["symbol"] == "MNQ"

    def test_journal_emits_fill_realized_event(
        self,
        sample_expected_ctx: ExpectedFillContext,
        temp_journal_path: Path,
    ) -> None:
        journal = EventJournal(temp_journal_path)
        rec = SlippageRecorder(journal=journal)
        rec.record_expected(sample_expected_ctx)

        rec.record_realized(
            order_id="test_order_1",
            realized_price=Decimal("18234.25"),
            realized_at=datetime(2026, 4, 14, 10, 0, 1, tzinfo=UTC),
            fill_qty=100,
        )

        # Replay and verify
        events = list(journal.replay(event_types=(FILL_REALIZED,)))
        assert len(events) == 1
        assert events[0].event_type == FILL_REALIZED
        assert events[0].payload["order_id"] == "test_order_1"
        assert events[0].payload["slippage_ticks"] == pytest.approx(1.0)

    def test_journal_emits_fill_orphaned_event(self, temp_journal_path: Path) -> None:
        journal = EventJournal(temp_journal_path)
        rec = SlippageRecorder(journal=journal)

        # Record realized without matching expectation
        rec.record_realized(
            order_id="unknown",
            realized_price=Decimal("18234.00"),
            realized_at=datetime.now(UTC),
            fill_qty=100,
        )

        # Check for orphaned event
        events = list(journal.replay(event_types=(FILL_ORPHANED,)))
        assert len(events) == 1
        assert events[0].payload["reason"] == "no_matching_expectation"

    def test_latency_ms_calculation(self, sample_expected_ctx: ExpectedFillContext) -> None:
        rec = SlippageRecorder()
        rec.record_expected(sample_expected_ctx)

        realized_at = sample_expected_ctx.submitted_at + timedelta(milliseconds=2500)
        result = rec.record_realized(
            order_id="test_order_1",
            realized_price=Decimal("18234.00"),
            realized_at=realized_at,
            fill_qty=100,
        )

        assert result is not None
        assert result.latency_ms == pytest.approx(2500.0)

    def test_partial_fills_accumulate(self, sample_expected_ctx: ExpectedFillContext) -> None:
        rec = SlippageRecorder()
        rec.record_expected(sample_expected_ctx)

        # First partial fill
        result1 = rec.record_realized(
            order_id="test_order_1",
            realized_price=Decimal("18234.00"),
            realized_at=datetime(2026, 4, 14, 10, 0, 1, tzinfo=UTC),
            fill_qty=60,
        )
        assert result1 is not None
        assert result1.fill_qty == 60

        # The expectation is now removed, so second fill should be orphaned
        result2 = rec.record_realized(
            order_id="test_order_1",
            realized_price=Decimal("18234.00"),
            realized_at=datetime(2026, 4, 14, 10, 0, 2, tzinfo=UTC),
            fill_qty=40,
        )
        assert result2 is None


class TestExportToDataframe:
    def test_export_empty_journal(self, temp_journal_path: Path) -> None:
        journal = EventJournal(temp_journal_path)
        df = export_to_dataframe(journal)
        assert df.height == 0
        assert "slippage_ticks" in df.columns

    def test_export_single_fill(
        self,
        sample_expected_ctx: ExpectedFillContext,
        temp_journal_path: Path,
    ) -> None:
        journal = EventJournal(temp_journal_path)
        rec = SlippageRecorder(journal=journal)
        rec.record_expected(sample_expected_ctx)
        rec.record_realized(
            order_id="test_order_1",
            realized_price=Decimal("18234.25"),
            realized_at=datetime(2026, 4, 14, 10, 0, 1, tzinfo=UTC),
            fill_qty=100,
        )

        df = export_to_dataframe(journal)
        assert df.height == 1
        assert df["order_id"][0] == "test_order_1"
        assert df["side"][0] == "long"
        assert df["slippage_ticks"][0] == pytest.approx(1.0)

    def test_export_has_required_columns(
        self,
        sample_expected_ctx: ExpectedFillContext,
        temp_journal_path: Path,
    ) -> None:
        journal = EventJournal(temp_journal_path)
        rec = SlippageRecorder(journal=journal)
        rec.record_expected(sample_expected_ctx)
        rec.record_realized(
            order_id="test_order_1",
            realized_price=Decimal("18234.25"),
            realized_at=datetime(2026, 4, 14, 10, 0, 1, tzinfo=UTC),
            fill_qty=100,
        )

        df = export_to_dataframe(journal)
        required_cols = [
            "order_id",
            "side",
            "expected_price",
            "realized_price",
            "slippage_ticks",
            "tod_bucket",
            "volatility_regime",
            "liquidity_proxy",
        ]
        for col in required_cols:
            assert col in df.columns
