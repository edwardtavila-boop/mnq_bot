"""Unit tests for OrderBook state machine.

Tests verify order lifecycle, state transitions, VWAP calculations,
journal replay, idempotency, and metrics emission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mnq.core.types import Side
from mnq.executor.orders import (
    Fill,
    OrderBook,
    OrderError,
    OrderState,
    OrderType,
)
from mnq.observability.metrics import reset_metrics_for_tests
from mnq.storage.journal import EventJournal


@pytest.fixture
def tmp_journal(tmp_path: Path) -> EventJournal:
    """Fixture providing a temporary EventJournal."""
    db_path = tmp_path / "test.db"
    return EventJournal(db_path, fsync=False)


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    """Reset metrics before each test."""
    reset_metrics_for_tests()


class TestOrderSubmit:
    """Tests for submit() operation."""

    def test_submit_creates_pending_order(self, tmp_journal: EventJournal) -> None:
        """Submit creates an order in PENDING state with a unique client_order_id."""
        book = OrderBook(tmp_journal)

        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )

        assert order.state == OrderState.PENDING
        assert order.client_order_id is not None
        assert len(order.client_order_id) == 32  # uuid4 hex
        assert order.venue_order_id is None
        assert order.filled_qty == 0
        assert order.avg_fill_price is None

    def test_submit_qty_validation(self, tmp_journal: EventJournal) -> None:
        """Submit raises OrderError if qty <= 0."""
        book = OrderBook(tmp_journal)

        with pytest.raises(OrderError, match="qty must be > 0"):
            book.submit(
                symbol="MNQ",
                side=Side.SHORT,
                qty=0,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("18000"),
            )

        with pytest.raises(OrderError, match="qty must be > 0"):
            book.submit(
                symbol="MNQ",
                side=Side.SHORT,
                qty=-5,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("18000"),
            )

    def test_submit_generates_unique_ids(self, tmp_journal: EventJournal) -> None:
        """Multiple submissions generate unique client_order_ids."""
        book = OrderBook(tmp_journal)

        order1 = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        order2 = book.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=2,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18500"),
        )

        assert order1.client_order_id != order2.client_order_id

    def test_submit_journaled(self, tmp_journal: EventJournal) -> None:
        """Submit writes ORDER_SUBMITTED event to journal."""
        book = OrderBook(tmp_journal)

        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=5,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18250.50"),
        )

        # Verify journal entry
        entries = tmp_journal.find_by_trace(order.trace_id)
        assert len(entries) == 1
        assert entries[0].event_type == "order.submitted"
        assert entries[0].payload["client_order_id"] == order.client_order_id


class TestOrderAck:
    """Tests for ack() operation."""

    def test_ack_pending_order(self, tmp_journal: EventJournal) -> None:
        """Ack transitions PENDING → WORKING."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )

        acked = book.ack(order.client_order_id, "venue-123")

        assert acked.state == OrderState.WORKING
        assert acked.venue_order_id == "venue-123"

    def test_ack_non_pending_raises(self, tmp_journal: EventJournal) -> None:
        """Ack raises if order is not PENDING."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")

        with pytest.raises(OrderError, match="expected PENDING"):
            book.ack(order.client_order_id, "venue-456")

    def test_ack_terminal_order_raises(self, tmp_journal: EventJournal) -> None:
        """Ack raises if order is terminal."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        book.reject(order.client_order_id, "rejected by broker")

        with pytest.raises(OrderError, match="Cannot ack terminal"):
            book.ack(order.client_order_id, "venue-123")


class TestOrderFill:
    """Tests for apply_fill() operation."""

    def test_apply_partial_fill(self, tmp_journal: EventJournal) -> None:
        """Partial fill updates filled_qty and state."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=10,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")

        filled = book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18250.50"),
                qty=4,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )

        assert filled.state == OrderState.PARTIAL
        assert filled.filled_qty == 4
        assert filled.remaining_qty == 6
        assert filled.avg_fill_price == Decimal("18250.50")

    def test_apply_full_fill(self, tmp_journal: EventJournal) -> None:
        """Fill matching qty transitions to FILLED."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=5,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")

        filled = book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18250.00"),
                qty=5,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )

        assert filled.state == OrderState.FILLED
        assert filled.filled_qty == 5
        assert filled.remaining_qty == 0
        assert filled.is_terminal

    def test_apply_vwap_calculation(self, tmp_journal: EventJournal) -> None:
        """VWAP is correctly calculated over multiple fills."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=10,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")

        # Fill 1: 3 @ 18250.00
        fill1 = book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18250.00"),
                qty=3,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )
        assert fill1.avg_fill_price == Decimal("18250.00")

        # Fill 2: 2 @ 18251.00 => VWAP = (3*18250 + 2*18251) / 5 = 18250.40
        fill2 = book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-2",
                price=Decimal("18251.00"),
                qty=2,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )
        expected_vwap = (
            Decimal("18250.00") * Decimal("3")
            + Decimal("18251.00") * Decimal("2")
        ) / Decimal("5")
        assert fill2.avg_fill_price == expected_vwap.quantize(
            Decimal("0.0001")
        )

        # Fill 3: 5 @ 18252.00
        fill3 = book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-3",
                price=Decimal("18252.00"),
                qty=5,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )
        # VWAP = (fill2.avg_fill_price * 5 + 18252 * 5) / 10
        expected_final = (
            fill2.avg_fill_price * Decimal("5") + Decimal("18252.00") * Decimal("5")
        ) / Decimal("10")
        assert fill3.avg_fill_price == expected_final.quantize(
            Decimal("0.0001")
        )
        assert fill3.is_terminal

    def test_duplicate_fill_idempotent(self, tmp_journal: EventJournal) -> None:
        """Duplicate venue_fill_id is a no-op (idempotent)."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=10,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")

        # First fill
        fill1 = book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18250.00"),
                qty=5,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )
        assert fill1.filled_qty == 5

        # Replay the same fill with the same venue_fill_id
        fill1_replay = book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18250.00"),
                qty=5,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )
        # Should still be 5, not 10
        assert fill1_replay.filled_qty == 5

    def test_fill_overfull_raises(self, tmp_journal: EventJournal) -> None:
        """Fill that exceeds order qty raises OrderError."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=5,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")

        with pytest.raises(OrderError, match="Fill overfull"):
            book.apply_fill(
                Fill(
                    client_order_id=order.client_order_id,
                    venue_fill_id="fill-1",
                    price=Decimal("18250.00"),
                    qty=10,
                    ts=datetime.now(UTC),
                    trace_id=None,
                )
            )

    def test_fill_terminal_order_raises(self, tmp_journal: EventJournal) -> None:
        """Fill on rejected/cancelled order raises OrderError."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=5,
            order_type=OrderType.MARKET,
        )
        book.reject(order.client_order_id, "rejected")

        with pytest.raises(OrderError, match="Cannot fill terminal"):
            book.apply_fill(
                Fill(
                    client_order_id=order.client_order_id,
                    venue_fill_id="fill-1",
                    price=Decimal("18250.00"),
                    qty=1,
                    ts=datetime.now(UTC),
                    trace_id=None,
                )
            )


class TestOrderReject:
    """Tests for reject() operation."""

    def test_reject_pending_order(self, tmp_journal: EventJournal) -> None:
        """Reject transitions PENDING → REJECTED."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )

        rejected = book.reject(order.client_order_id, "insufficient funds")

        assert rejected.state == OrderState.REJECTED
        assert rejected.is_terminal

    def test_reject_terminal_order_raises(self, tmp_journal: EventJournal) -> None:
        """Reject raises if order is already terminal."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        book.reject(order.client_order_id, "first rejection")

        with pytest.raises(OrderError, match="Cannot reject terminal"):
            book.reject(order.client_order_id, "second rejection")


class TestOrderCancel:
    """Tests for cancel() operation."""

    def test_cancel_working_order(self, tmp_journal: EventJournal) -> None:
        """Cancel transitions WORKING → CANCELLED."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")

        cancelled = book.cancel(order.client_order_id, reason="user cancel")

        assert cancelled.state == OrderState.CANCELLED
        assert cancelled.is_terminal

    def test_cancel_partial_order(self, tmp_journal: EventJournal) -> None:
        """Cancel transitions PARTIAL → CANCELLED."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=10,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")
        book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18250.00"),
                qty=5,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )

        cancelled = book.cancel(order.client_order_id)

        assert cancelled.state == OrderState.CANCELLED
        assert cancelled.filled_qty == 5  # Partial fill preserved

    def test_cancel_terminal_order_raises(self, tmp_journal: EventJournal) -> None:
        """Cancel raises if order is already terminal."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        book.reject(order.client_order_id, "rejected")

        with pytest.raises(OrderError, match="Cannot cancel terminal"):
            book.cancel(order.client_order_id)


class TestOrderBookQueries:
    """Tests for query methods (get, open_orders, all_orders)."""

    def test_get_order(self, tmp_journal: EventJournal) -> None:
        """Get retrieves an order by client_order_id."""
        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )

        retrieved = book.get(order.client_order_id)

        assert retrieved is not None
        assert retrieved.client_order_id == order.client_order_id

    def test_get_nonexistent_order(self, tmp_journal: EventJournal) -> None:
        """Get returns None for nonexistent order."""
        book = OrderBook(tmp_journal)

        result = book.get("nonexistent-id")

        assert result is None

    def test_open_orders_filters_terminal(self, tmp_journal: EventJournal) -> None:
        """open_orders() excludes terminal orders."""
        book = OrderBook(tmp_journal)

        order1 = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        order2 = book.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=2,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18500"),
        )
        order3 = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=3,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18000"),
        )

        # Reject one
        book.reject(order2.client_order_id, "rejected")

        open_orders = book.open_orders()

        assert len(open_orders) == 2
        assert all(not o.is_terminal for o in open_orders)
        assert order1.client_order_id in {o.client_order_id for o in open_orders}
        assert order3.client_order_id in {o.client_order_id for o in open_orders}  # noqa: F841

    def test_all_orders(self, tmp_journal: EventJournal) -> None:
        """all_orders() returns all orders."""
        book = OrderBook(tmp_journal)

        book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        order2 = book.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=2,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18500"),
        )
        book.reject(order2.client_order_id, "rejected")

        all_orders = book.all_orders()

        assert len(all_orders) == 2


class TestOrderBookJournalReplay:
    """Tests for from_journal() reconstruction."""

    def test_replay_submit_ack_partial_fill(self, tmp_journal: EventJournal) -> None:
        """Replay reconstructs order through multiple state transitions."""
        # Build a sequence: submit → ack → partial fill
        book1 = OrderBook(tmp_journal)
        order = book1.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18250.00"),
        )
        book1.ack(order.client_order_id, "venue-123")
        book1.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18250.00"),
                qty=6,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )

        # Replay from journal
        book2 = OrderBook.from_journal(tmp_journal)
        replayed = book2.get(order.client_order_id)

        assert replayed is not None
        assert replayed.state == OrderState.PARTIAL
        assert replayed.filled_qty == 6
        assert replayed.venue_order_id == "venue-123"
        assert replayed.avg_fill_price == Decimal("18250.00")

    def test_replay_full_sequence(self, tmp_journal: EventJournal) -> None:
        """Replay reconstructs full lifecycle: submit → ack → 2 fills → filled."""
        book1 = OrderBook(tmp_journal)
        order = book1.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=8,
            order_type=OrderType.MARKET,
        )
        book1.ack(order.client_order_id, "venue-456")
        book1.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18260.00"),
                qty=3,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )
        final_fill = book1.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-2",
                price=Decimal("18261.00"),
                qty=5,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )

        # Replay
        book2 = OrderBook.from_journal(tmp_journal)
        replayed = book2.get(order.client_order_id)

        assert replayed is not None
        assert replayed.state == OrderState.FILLED
        assert replayed.filled_qty == 8
        assert replayed.avg_fill_price == final_fill.avg_fill_price

    def test_replay_multiple_orders(self, tmp_journal: EventJournal) -> None:
        """Replay correctly reconstructs multiple independent orders."""
        book1 = OrderBook(tmp_journal)

        order1 = book1.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=2,
            order_type=OrderType.MARKET,
        )
        order2 = book1.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=3,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("18500"),
        )

        book1.ack(order1.client_order_id, "venue-1")
        book1.reject(order2.client_order_id, "insufficient funds")

        # Replay
        book2 = OrderBook.from_journal(tmp_journal)

        replayed1 = book2.get(order1.client_order_id)
        replayed2 = book2.get(order2.client_order_id)

        assert replayed1 is not None
        assert replayed1.state == OrderState.WORKING

        assert replayed2 is not None
        assert replayed2.state == OrderState.REJECTED


class TestOrderBookMetrics:
    """Tests for metrics emission."""

    def test_submit_emits_metric(self, tmp_journal: EventJournal) -> None:
        """Submit increments orders_submitted_total metric."""
        from mnq.observability.metrics import orders_submitted_total

        book = OrderBook(tmp_journal)

        book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )

        # Get the metric value
        samples = orders_submitted_total.collect()[0].samples
        metric_data = [
            s for s in samples
            if s.name == "orders_submitted_total"
            and s.labels == {"side": "long", "order_type": "market"}
        ]
        assert len(metric_data) == 1
        assert metric_data[0].value > 0

    def test_fill_emits_metric(self, tmp_journal: EventJournal) -> None:
        """Full fill increments orders_filled_total metric."""
        from mnq.observability.metrics import orders_filled_total

        book = OrderBook(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=5,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "venue-123")

        book.apply_fill(
            Fill(
                client_order_id=order.client_order_id,
                venue_fill_id="fill-1",
                price=Decimal("18250.00"),
                qty=5,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )

        samples = orders_filled_total.collect()[0].samples
        metric_data = [
            s for s in samples
            if s.name == "orders_filled_total"
            and s.labels == {"side": "short", "exit_reason": "filled"}
        ]
        assert len(metric_data) == 1
        assert metric_data[0].value > 0
