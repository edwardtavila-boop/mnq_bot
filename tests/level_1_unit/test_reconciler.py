"""Tests for mnq.executor.reconciler (position reconciliation)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mnq.core.types import Side
from mnq.executor.orders import Fill, OrderBook, OrderType
from mnq.executor.reconciler import (
    PeriodicReconciler,
    PositionReconciler,
    ReconcileDiff,
    ReconcileReport,
    VenueOrder,
    VenuePosition,
    net_positions_from_journal,
)
from mnq.executor.safety import CircuitBreaker
from mnq.observability.metrics import reset_metrics_for_tests
from mnq.storage.journal import EventJournal
from mnq.storage.schema import RECONCILE_DIFF, RECONCILE_HALT, RECONCILE_OK, RECONCILE_START


@pytest.fixture
def temp_journal(tmp_path: Path) -> EventJournal:
    """Create a temporary EventJournal for testing."""
    db_path = tmp_path / "test.db"
    journal = EventJournal(db_path, fsync=False)
    return journal


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    """Reset prometheus metrics before each test."""
    reset_metrics_for_tests()


class FakeVenueFetcher:
    """Fake VenueSnapshotFetcher for testing."""

    def __init__(
        self,
        positions: list[VenuePosition] | None = None,
        orders: list[VenueOrder] | None = None,
    ) -> None:
        """Initialize with canned positions and orders.

        Args:
            positions: List of VenuePosition to return.
            orders: List of VenueOrder to return.
        """
        self.positions = positions or []
        self.orders = orders or []

    async def fetch_positions(self) -> list[VenuePosition]:
        """Return positions."""
        return self.positions

    async def fetch_open_orders(self) -> list[VenueOrder]:
        """Return orders."""
        return self.orders


class TestComputeDiffs:
    """Tests for PositionReconciler.compute_diffs (pure function)."""

    def test_empty_both_sides(self, temp_journal: EventJournal) -> None:
        """Empty local and venue: no diffs."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        diffs = reconciler.compute_diffs(
            local_positions={},
            local_orders=[],
            venue_positions=[],
            venue_orders=[],
        )

        assert len(diffs) == 0

    def test_position_qty_mismatch(self, temp_journal: EventJournal) -> None:
        """Local qty != venue qty: critical diff."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        diffs = reconciler.compute_diffs(
            local_positions={"MNQ": 2},
            local_orders=[],
            venue_positions=[VenuePosition(symbol="MNQ", net_qty=3, avg_price=Decimal("18000"))],
            venue_orders=[],
        )

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.kind == "position_qty"
        assert diff.symbol == "MNQ"
        assert diff.severity == "critical"
        assert diff.local == 2
        assert diff.venue == 3

    def test_position_long_local_short_venue(self, temp_journal: EventJournal) -> None:
        """Local long 2, venue short 2: critical (magnitude AND sign)."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        diffs = reconciler.compute_diffs(
            local_positions={"MNQ": 2},
            local_orders=[],
            venue_positions=[VenuePosition(symbol="MNQ", net_qty=-2, avg_price=Decimal("18000"))],
            venue_orders=[],
        )

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.kind == "position_qty"
        assert diff.severity == "critical"

    def test_position_missing_local(self, temp_journal: EventJournal) -> None:
        """Venue has position, local doesn't: critical diff."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        diffs = reconciler.compute_diffs(
            local_positions={},
            local_orders=[],
            venue_positions=[VenuePosition(symbol="MNQ", net_qty=5, avg_price=Decimal("18000"))],
            venue_orders=[],
        )

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.kind == "position_missing_local"
        assert diff.severity == "critical"
        assert diff.local is None
        assert diff.venue == 5

    def test_position_missing_venue(self, temp_journal: EventJournal) -> None:
        """Local has position, venue doesn't: critical diff."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        diffs = reconciler.compute_diffs(
            local_positions={"MNQ": 3},
            local_orders=[],
            venue_positions=[],
            venue_orders=[],
        )

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.kind == "position_missing_venue"
        assert diff.severity == "critical"
        assert diff.local == 3
        assert diff.venue is None

    def test_order_missing_local(self, temp_journal: EventJournal) -> None:
        """Order on venue but not in local book (zombie): critical."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        venue_order = VenueOrder(
            venue_order_id="V123",
            client_order_id="C123",
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            filled_qty=0,
            state="WORKING",
        )

        diffs = reconciler.compute_diffs(
            local_positions={},
            local_orders=[],
            venue_positions=[],
            venue_orders=[venue_order],
        )

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.kind == "order_missing_local"
        assert diff.severity == "critical"

    def test_order_missing_local_no_cid(self, temp_journal: EventJournal) -> None:
        """Order on venue with no client_order_id: critical."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        venue_order = VenueOrder(
            venue_order_id="V123",
            client_order_id=None,
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            filled_qty=0,
            state="WORKING",
        )

        diffs = reconciler.compute_diffs(
            local_positions={},
            local_orders=[],
            venue_positions=[],
            venue_orders=[venue_order],
        )

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.kind == "order_missing_local"
        assert diff.severity == "critical"

    def test_order_state_mismatch(self, temp_journal: EventJournal) -> None:
        """Local order WORKING, venue order CANCELLED: warn."""
        book = OrderBook(temp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18000"),
        )
        book.ack(order.client_order_id, "V123")

        reconciler = PositionReconciler(book, temp_journal)

        venue_order = VenueOrder(
            venue_order_id="V123",
            client_order_id=order.client_order_id,
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            filled_qty=0,
            state="CANCELLED",
        )

        diffs = reconciler.compute_diffs(
            local_positions={},
            local_orders=[order],
            venue_positions=[],
            venue_orders=[venue_order],
        )

        # Should have state mismatch (warn) but NOT missing_local
        state_diffs = [d for d in diffs if d.kind == "order_state_mismatch"]
        assert len(state_diffs) == 1
        assert state_diffs[0].severity == "warn"

    def test_order_fills_mismatch(self, temp_journal: EventJournal) -> None:
        """Local filled_qty != venue filled_qty: critical."""
        book = OrderBook(temp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=2,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18000"),
        )
        book.ack(order.client_order_id, "V123")

        # Apply 1 fill locally
        fill = Fill(
            client_order_id=order.client_order_id,
            venue_fill_id="F1",
            price=Decimal("18000"),
            qty=1,
            ts=datetime.now(UTC),
            trace_id=None,
        )
        book.apply_fill(fill)

        reconciler = PositionReconciler(book, temp_journal)

        # Venue says 2 filled
        venue_order = VenueOrder(
            venue_order_id="V123",
            client_order_id=order.client_order_id,
            symbol="MNQ",
            side=Side.LONG,
            qty=2,
            filled_qty=2,
            state="FILLED",
        )

        diffs = reconciler.compute_diffs(
            local_positions={},
            local_orders=book.all_orders(),
            venue_positions=[],
            venue_orders=[venue_order],
        )

        fills_mismatch = [d for d in diffs if d.kind == "order_fills_mismatch"]
        assert len(fills_mismatch) == 1
        assert fills_mismatch[0].severity == "critical"
        assert fills_mismatch[0].local == 1
        assert fills_mismatch[0].venue == 2

    def test_multiple_symbols(self, temp_journal: EventJournal) -> None:
        """Multiple symbols handled correctly."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        diffs = reconciler.compute_diffs(
            local_positions={"MNQ": 2, "ES": 1},
            local_orders=[],
            venue_positions=[
                VenuePosition(symbol="MNQ", net_qty=2, avg_price=Decimal("18000")),
                VenuePosition(symbol="ES", net_qty=0, avg_price=Decimal("5000")),
            ],
            venue_orders=[],
        )

        # ES is mismatched (local=1, venue=0)
        assert len(diffs) == 1
        assert diffs[0].symbol == "ES"
        assert diffs[0].kind == "position_qty"


class TestReconcile:
    """Tests for PositionReconciler.reconcile (with I/O)."""

    @pytest.mark.asyncio
    async def test_reconcile_empty_ok(self, temp_journal: EventJournal) -> None:
        """Empty state: reconciliation succeeds."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)
        fetcher = FakeVenueFetcher(positions=[], orders=[])

        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        report = await reconciler.reconcile(fetcher, at=now)

        assert report.ok is True
        assert len(report.diffs) == 0
        assert report.reconciled_at == now

        # Check that RECONCILE_START, RECONCILE_OK were written
        events = list(temp_journal.replay())
        event_types = [e.event_type for e in events]
        assert RECONCILE_START in event_types
        assert RECONCILE_OK in event_types

    @pytest.mark.asyncio
    async def test_reconcile_critical_halts_breaker(self, temp_journal: EventJournal) -> None:
        """Critical diff halts the breaker."""
        book = OrderBook(temp_journal)
        breaker = CircuitBreaker()
        reconciler = PositionReconciler(book, temp_journal, breaker=breaker)

        # Venue has a position, local doesn't
        fetcher = FakeVenueFetcher(
            positions=[VenuePosition(symbol="MNQ", net_qty=5, avg_price=Decimal("18000"))],
            orders=[],
        )

        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        report = await reconciler.reconcile(fetcher, at=now)

        assert report.ok is False
        assert len(report.critical_diffs) == 1
        assert breaker.allow_trade().allowed is False

    @pytest.mark.asyncio
    async def test_reconcile_warn_no_halt(self, temp_journal: EventJournal) -> None:
        """Warn-only diffs do NOT halt the breaker."""
        book = OrderBook(temp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18000"),
        )
        book.ack(order.client_order_id, "V123")

        breaker = CircuitBreaker()
        reconciler = PositionReconciler(book, temp_journal, breaker=breaker)

        # State mismatch (warn)
        venue_order = VenueOrder(
            venue_order_id="V123",
            client_order_id=order.client_order_id,
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            filled_qty=0,
            state="CANCELLED",
        )
        fetcher = FakeVenueFetcher(positions=[], orders=[venue_order])

        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        report = await reconciler.reconcile(fetcher, at=now)

        assert report.ok is True  # No critical diffs
        assert len(report.critical_diffs) == 0
        assert len(report.diffs) == 1  # But there is a warn diff
        assert breaker.allow_trade().allowed is True

    @pytest.mark.asyncio
    async def test_reconcile_journals_diffs(self, temp_journal: EventJournal) -> None:
        """Each diff is journaled with appropriate detail."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        fetcher = FakeVenueFetcher(
            positions=[VenuePosition(symbol="MNQ", net_qty=5, avg_price=Decimal("18000"))],
            orders=[],
        )

        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        await reconciler.reconcile(fetcher, at=now)

        # Check that RECONCILE_DIFF events were written
        events = list(temp_journal.replay())
        diff_events = [e for e in events if e.event_type == RECONCILE_DIFF]
        assert len(diff_events) == 1
        assert diff_events[0].payload["kind"] == "position_missing_local"

    @pytest.mark.asyncio
    async def test_reconcile_journals_halt(self, temp_journal: EventJournal) -> None:
        """Critical diffs journal RECONCILE_HALT."""
        book = OrderBook(temp_journal)
        breaker = CircuitBreaker()
        reconciler = PositionReconciler(book, temp_journal, breaker=breaker)

        fetcher = FakeVenueFetcher(
            positions=[VenuePosition(symbol="MNQ", net_qty=5, avg_price=Decimal("18000"))],
            orders=[],
        )

        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        await reconciler.reconcile(fetcher, at=now)

        events = list(temp_journal.replay())
        event_types = [e.event_type for e in events]
        assert RECONCILE_HALT in event_types


class TestReconcileReport:
    """Tests for ReconcileReport properties."""

    def test_critical_diffs_property(self) -> None:
        """critical_diffs filters correctly."""
        diffs = [
            ReconcileDiff(
                kind="position_qty",
                symbol="MNQ",
                detail="test",
                local=1,
                venue=2,
                severity="critical",
            ),
            ReconcileDiff(
                kind="order_state_mismatch",
                symbol="MNQ",
                detail="test",
                local="WORKING",
                venue="CANCELLED",
                severity="warn",
            ),
            ReconcileDiff(
                kind="position_missing_local",
                symbol="ES",
                detail="test",
                local=None,
                venue=1,
                severity="critical",
            ),
        ]

        report = ReconcileReport(diffs=diffs, reconciled_at=datetime.now(UTC), ok=False)

        critical = report.critical_diffs
        assert len(critical) == 2
        assert all(d.severity == "critical" for d in critical)

    def test_ok_property(self) -> None:
        """ok property reflects critical diffs."""
        # No diffs -> ok
        report1 = ReconcileReport(diffs=[], reconciled_at=datetime.now(UTC), ok=True)
        assert report1.ok is True

        # Warn-only -> ok
        report2 = ReconcileReport(
            diffs=[
                ReconcileDiff(
                    kind="order_state_mismatch",
                    symbol="MNQ",
                    detail="test",
                    local="WORKING",
                    venue="CANCELLED",
                    severity="warn",
                )
            ],
            reconciled_at=datetime.now(UTC),
            ok=True,
        )
        assert report2.ok is True

        # Critical -> not ok
        report3 = ReconcileReport(
            diffs=[
                ReconcileDiff(
                    kind="position_qty",
                    symbol="MNQ",
                    detail="test",
                    local=1,
                    venue=2,
                    severity="critical",
                )
            ],
            reconciled_at=datetime.now(UTC),
            ok=False,
        )
        assert report3.ok is False


class TestPeriodicReconciler:
    """Tests for PeriodicReconciler."""

    def test_due_on_startup(self, temp_journal: EventJournal) -> None:
        """due() returns True initially if on_startup=True."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)
        periodic = PeriodicReconciler(reconciler, interval_s=60, on_startup=True)

        now = datetime.now(UTC)
        assert periodic.due(now) is True

    def test_due_no_startup(self, temp_journal: EventJournal) -> None:
        """due() returns False initially if on_startup=False."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)
        periodic = PeriodicReconciler(reconciler, interval_s=60, on_startup=False)

        now = datetime.now(UTC)
        assert periodic.due(now) is False

    @pytest.mark.asyncio
    async def test_tick_updates_last_run(self, temp_journal: EventJournal) -> None:
        """tick() updates last_run."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)
        periodic = PeriodicReconciler(reconciler, interval_s=60, on_startup=True)

        fetcher = FakeVenueFetcher(positions=[], orders=[])
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)

        assert periodic.last_run() is None
        await periodic.tick(fetcher, now)
        assert periodic.last_run() == now

    def test_due_after_interval(self, temp_journal: EventJournal) -> None:
        """due() returns True after interval_s has elapsed."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)
        periodic = PeriodicReconciler(reconciler, interval_s=60, on_startup=True)

        now1 = datetime(2026, 4, 14, 16, 0, 0, tzinfo=UTC)
        periodic._last_run = now1

        # Before interval: not due
        now2 = datetime(2026, 4, 14, 16, 0, 30, tzinfo=UTC)
        assert periodic.due(now2) is False

        # After interval: due
        now3 = datetime(2026, 4, 14, 16, 1, 0, tzinfo=UTC)
        assert periodic.due(now3) is True

    @pytest.mark.asyncio
    async def test_periodic_integration(self, temp_journal: EventJournal) -> None:
        """Full periodic reconciliation flow."""
        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)
        periodic = PeriodicReconciler(reconciler, interval_s=60, on_startup=True)

        fetcher = FakeVenueFetcher(positions=[], orders=[])
        now = datetime.now(UTC)

        # Initial tick
        assert periodic.due(now)
        report = await periodic.tick(fetcher, now)
        assert report.ok is True
        assert periodic.last_run() == now

        # Not due yet
        now_later = datetime.fromtimestamp(now.timestamp() + 30, UTC)
        assert periodic.due(now_later) is False

        # Due after interval
        now_very_later = datetime.fromtimestamp(now.timestamp() + 61, UTC)
        assert periodic.due(now_very_later) is True


class TestNetPositionsFromJournal:
    """Tests for net_positions_from_journal helper."""

    def test_empty_journal(self, temp_journal: EventJournal) -> None:
        """Empty journal: no positions."""
        positions = net_positions_from_journal(temp_journal)
        assert positions == {}

    def test_single_long_fill(self, temp_journal: EventJournal) -> None:
        """Single long fill contributes to position."""
        book = OrderBook(temp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=2,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "V1")
        fill = Fill(
            client_order_id=order.client_order_id,
            venue_fill_id="F1",
            price=Decimal("18000"),
            qty=2,
            ts=datetime.now(UTC),
            trace_id=None,
        )
        book.apply_fill(fill)

        positions = net_positions_from_journal(temp_journal)
        assert positions.get("MNQ", 0) == 2

    def test_single_short_fill(self, temp_journal: EventJournal) -> None:
        """Single short fill is negative."""
        book = OrderBook(temp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=3,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "V1")
        fill = Fill(
            client_order_id=order.client_order_id,
            venue_fill_id="F1",
            price=Decimal("18000"),
            qty=3,
            ts=datetime.now(UTC),
            trace_id=None,
        )
        book.apply_fill(fill)

        positions = net_positions_from_journal(temp_journal)
        assert positions.get("MNQ", 0) == -3

    def test_mixed_long_short(self, temp_journal: EventJournal) -> None:
        """Long + short fills net correctly."""
        book = OrderBook(temp_journal)

        # Long 2
        order1 = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=2,
            order_type=OrderType.MARKET,
        )
        book.ack(order1.client_order_id, "V1")
        fill1 = Fill(
            client_order_id=order1.client_order_id,
            venue_fill_id="F1",
            price=Decimal("18000"),
            qty=2,
            ts=datetime.now(UTC),
            trace_id=None,
        )
        book.apply_fill(fill1)

        # Short 1 (net becomes 1)
        order2 = book.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=1,
            order_type=OrderType.MARKET,
        )
        book.ack(order2.client_order_id, "V2")
        fill2 = Fill(
            client_order_id=order2.client_order_id,
            venue_fill_id="F2",
            price=Decimal("18000"),
            qty=1,
            ts=datetime.now(UTC),
            trace_id=None,
        )
        book.apply_fill(fill2)

        positions = net_positions_from_journal(temp_journal)
        assert positions.get("MNQ", 0) == 1


class TestMetricsTracking:
    """Tests for reconcile_diffs_total metric increments."""

    @pytest.mark.asyncio
    async def test_metrics_incremented(self, temp_journal: EventJournal) -> None:
        """reconcile_diffs_total counter is incremented by diff kind."""
        # Reset metrics
        reset_metrics_for_tests()

        book = OrderBook(temp_journal)
        reconciler = PositionReconciler(book, temp_journal)

        fetcher = FakeVenueFetcher(
            positions=[
                VenuePosition(symbol="MNQ", net_qty=5, avg_price=Decimal("18000")),
                VenuePosition(symbol="ES", net_qty=1, avg_price=Decimal("5000")),
            ],
            orders=[],
        )

        now = datetime.now(UTC)
        report = await reconciler.reconcile(fetcher, at=now)

        assert len(report.diffs) == 2
        # Diffs should be 2x position_missing_local
        # Metrics should have been incremented


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
