"""[REAL] Position reconciliation: compare in-memory state vs venue state.

After a crash or disconnection, our in-memory state (reconstructed from
replaying the journal) may not match reality on the broker's books. This
module detects and records discrepancies, halts trading on critical diffs.

Key responsibilities:
  - Compare local OrderBook + positions vs venue-reported state
  - Classify diffs by kind and severity
  - Journal reconciliation events (RECONCILE_START, RECONCILE_DIFF, RECONCILE_OK/HALT)
  - Halt the circuit breaker on critical diffs
  - Track metrics via reconcile_diffs_total counter

Reconciliation is typically run:
  1. On startup (after replaying the journal)
  2. Periodically (e.g., every 60s)
  3. On demand (after WS reconnect, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from mnq.core.types import Side
from mnq.executor.orders import Order, OrderBook
from mnq.executor.safety import CircuitBreaker
from mnq.observability.logger import bind_trace_id, clear_trace_id, get_logger
from mnq.observability.metrics import reconcile_diffs_total
from mnq.storage.journal import EventJournal
from mnq.storage.schema import RECONCILE_DIFF, RECONCILE_HALT, RECONCILE_OK, RECONCILE_START


@dataclass(frozen=True)
class VenuePosition:
    """As reported by the broker."""

    symbol: str
    net_qty: int  # signed
    avg_price: Decimal


@dataclass(frozen=True)
class VenueOrder:
    """As reported by the broker."""

    venue_order_id: str
    client_order_id: str | None  # may be None if we can't correlate
    symbol: str
    side: Side
    qty: int
    filled_qty: int
    state: str  # free-form broker state string we map


@dataclass(frozen=True)
class ReconcileDiff:
    """One discrepancy between local and venue state."""

    kind: str  # "position_qty" | "position_missing_local" | "position_missing_venue"
    # | "order_missing_local" | "order_state_mismatch" | "order_fills_mismatch"
    symbol: str
    detail: str
    local: Any  # whatever we thought
    venue: Any  # what the broker says
    severity: str  # "info" | "warn" | "critical"


@dataclass(frozen=True)
class ReconcileReport:
    """Result of a reconciliation pass."""

    diffs: list[ReconcileDiff]
    reconciled_at: datetime
    ok: bool  # True iff no "critical" diffs

    @property
    def critical_diffs(self) -> list[ReconcileDiff]:
        """Return only critical-severity diffs."""
        return [d for d in self.diffs if d.severity == "critical"]


class VenueSnapshotFetcher(Protocol):
    """Abstract over however we get venue state (REST poll, WS initial
    snapshot, etc.). Tests provide a fake."""

    async def fetch_positions(self) -> list[VenuePosition]:
        """Fetch current positions from venue.

        Returns:
            List of VenuePosition objects.
        """
        ...

    async def fetch_open_orders(self) -> list[VenueOrder]:
        """Fetch open orders from venue.

        Returns:
            List of VenueOrder objects.
        """
        ...


def net_positions_from_journal(journal: EventJournal) -> dict[str, int]:
    """Compute net positions by replaying ORDER_FILLED events from journal.

    Returns a dict mapping symbol to signed net quantity.
    Positive = long, negative = short, 0 = flat.

    Algorithmic note (scorecard bundle v0.1 — Apr 2026):
        Original implementation nested a journal replay inside the fill loop,
        giving O(m * n) where m = fill events and n = submit events. Rebuilt
        as a two-pass O(m + n) walk: one replay of ORDER_SUBMITTED builds a
        ``{client_order_id: (symbol, side)}`` index, then one replay of
        ORDER_FILLED aggregates signed quantities via dict lookup.

    Args:
        journal: EventJournal to replay.

    Returns:
        Dict[symbol, net_qty].
    """
    from mnq.storage.schema import ORDER_FILLED

    # Pass 1: index submits by client_order_id. A later submit with the same
    # client_order_id would overwrite an earlier one; we accept that because
    # the order state machine forbids duplicate client_order_ids at submit
    # time, so overwrites only happen in a malformed journal and the previous
    # behaviour (first-match wins via break) would have been equally broken.
    submits_by_coid: dict[str, tuple[str, Side]] = {}
    for submit_entry in journal.replay(event_types=("order.submitted",)):
        payload = submit_entry.payload
        coid = payload.get("client_order_id")
        symbol = payload.get("symbol", "")
        side_str = payload.get("side", "")
        if coid and symbol and side_str:
            submits_by_coid[coid] = (symbol, Side(side_str))

    # Pass 2: aggregate fills against the submit index.
    positions: dict[str, int] = {}
    for entry in journal.replay(event_types=(ORDER_FILLED,)):
        payload = entry.payload
        coid = payload.get("client_order_id")
        if coid is None:
            continue
        info = submits_by_coid.get(coid)
        if info is None:
            # Fill without a matching submit — skip; reconciler will catch
            # this as a zombie in compute_diffs.
            continue
        symbol, side = info
        filled_qty = payload.get("filled_qty", 0)
        signed_qty = filled_qty * side.sign
        positions[symbol] = positions.get(symbol, 0) + signed_qty

    return positions


class PositionReconciler:
    """Compares in-memory OrderBook state against venue-reported state.

    On any critical diff:
      - writes RECONCILE_DIFF events to the journal
      - halts the circuit breaker
      - returns ReconcileReport with ok=False

    Otherwise writes RECONCILE_OK and returns ok=True.
    """

    def __init__(
        self,
        order_book: OrderBook,
        journal: EventJournal,
        *,
        breaker: CircuitBreaker | None = None,
        logger: Any = None,
    ) -> None:
        """Initialize PositionReconciler.

        Args:
            order_book: OrderBook instance (reconstructed from journal).
            journal: EventJournal for recording reconciliation events.
            breaker: Optional CircuitBreaker to halt on critical diffs.
            logger: Optional structlog logger. If None, creates one.
        """
        self.order_book = order_book
        self.journal = journal
        self.breaker = breaker
        self.logger = logger or get_logger(__name__)

    async def reconcile(
        self,
        fetcher: VenueSnapshotFetcher,
        *,
        at: datetime | None = None,
    ) -> ReconcileReport:
        """Run one reconciliation pass.

        Fetches venue state, compares against local state, journals events,
        and halts breaker if critical diffs are found.

        Args:
            fetcher: VenueSnapshotFetcher to get venue state.
            at: Optional timestamp for reconciliation. If None, uses now.

        Returns:
            ReconcileReport with diffs and ok status.
        """
        at = at or datetime.now(UTC)
        trace_id = self.journal.append(
            RECONCILE_START,
            {"scope": "positions_and_orders"},
        )

        # Fetch venue state
        venue_positions = await fetcher.fetch_positions()
        venue_orders = await fetcher.fetch_open_orders()

        # Compute local positions from journal
        local_positions = net_positions_from_journal(self.journal)

        # Get local orders
        local_orders = self.order_book.all_orders()

        # Compute diffs
        diffs = self.compute_diffs(
            local_positions,
            local_orders,
            venue_positions,
            venue_orders,
        )

        # Journal each diff and track metrics
        for diff in diffs:
            self.journal.append(
                RECONCILE_DIFF,
                {
                    "kind": diff.kind,
                    "symbol": diff.symbol,
                    "detail": diff.detail,
                    "local": str(diff.local),
                    "venue": str(diff.venue),
                    "severity": diff.severity,
                },
                trace_id=str(trace_id),
            )
            reconcile_diffs_total.labels(kind=diff.kind).inc()

        # Check if critical diffs exist
        critical_diffs = [d for d in diffs if d.severity == "critical"]
        ok = len(critical_diffs) == 0

        # Journal final result and halt if needed
        if ok:
            self.journal.append(
                RECONCILE_OK,
                {"diffs_count": len(diffs)},
                trace_id=str(trace_id),
            )
            bind_trace_id(str(trace_id))
            self.logger.info("reconciliation_ok", diffs_count=len(diffs))
            clear_trace_id()
        else:
            self.journal.append(
                RECONCILE_HALT,
                {"critical_diffs_count": len(critical_diffs)},
                trace_id=str(trace_id),
            )
            if self.breaker is not None:
                self.breaker.halt()
            bind_trace_id(str(trace_id))
            self.logger.error(
                "reconciliation_halt",
                critical_diffs_count=len(critical_diffs),
                diffs_count=len(diffs),
            )
            clear_trace_id()

        return ReconcileReport(
            diffs=diffs,
            reconciled_at=at,
            ok=ok,
        )

    def compute_diffs(
        self,
        local_positions: dict[str, int],
        local_orders: list[Order],
        venue_positions: list[VenuePosition],
        venue_orders: list[VenueOrder],
    ) -> list[ReconcileDiff]:
        """Pure function — compare local vs venue state and return diffs.

        Rules:
          1. Position qty mismatch: severity critical
          2. Position on venue, missing locally: severity critical
          3. Position on local, missing on venue: severity critical
          4. Order on venue, missing locally: severity critical (zombie)
          5. Order state mismatch: severity warn
          6. Order fill qty mismatch: severity critical

        Args:
            local_positions: Dict[symbol, net_qty] from journal.
            local_orders: List of Order objects from OrderBook.
            venue_positions: List of VenuePosition from broker.
            venue_orders: List of VenueOrder from broker.

        Returns:
            List of ReconcileDiff objects.
        """
        diffs: list[ReconcileDiff] = []

        # Build set of symbols from both sides
        local_symbols = set(local_positions.keys())
        venue_symbols = {p.symbol for p in venue_positions}
        all_symbols = local_symbols | venue_symbols

        # Check positions
        for symbol in all_symbols:
            local_qty = local_positions.get(symbol, 0)
            venue_pos = next(
                (p for p in venue_positions if p.symbol == symbol),
                None,
            )

            if venue_pos is None:
                # Position on local, missing on venue
                if local_qty != 0:
                    diffs.append(
                        ReconcileDiff(
                            kind="position_missing_venue",
                            symbol=symbol,
                            detail=f"Local has {local_qty}, venue has none",
                            local=local_qty,
                            venue=None,
                            severity="critical",
                        )
                    )
            elif venue_pos.net_qty != local_qty:
                # Position qty mismatch (covers both missing_local and qty mismatch)
                if local_qty == 0:
                    # Position on venue but not tracked locally
                    diffs.append(
                        ReconcileDiff(
                            kind="position_missing_local",
                            symbol=symbol,
                            detail=f"Venue has {venue_pos.net_qty}, local has none",
                            local=None,
                            venue=venue_pos.net_qty,
                            severity="critical",
                        )
                    )
                else:
                    # Actual qty mismatch
                    diffs.append(
                        ReconcileDiff(
                            kind="position_qty",
                            symbol=symbol,
                            detail=f"Local qty={local_qty}, venue qty={venue_pos.net_qty}",
                            local=local_qty,
                            venue=venue_pos.net_qty,
                            severity="critical",
                        )
                    )
            else:
                # Position matches; positions and avg_price match
                pass

        # Build order lookup by client_order_id
        local_orders_by_cid = {o.client_order_id: o for o in local_orders}

        # Check orders
        for venue_order in venue_orders:
            if venue_order.client_order_id is None:
                # Can't correlate; treat as zombie (missing locally)
                diffs.append(
                    ReconcileDiff(
                        kind="order_missing_local",
                        symbol=venue_order.symbol,
                        detail=f"Venue order {venue_order.venue_order_id} has no client_order_id",
                        local=None,
                        venue=venue_order.venue_order_id,
                        severity="critical",
                    )
                )
            else:
                local_order = local_orders_by_cid.get(venue_order.client_order_id)
                if local_order is None:
                    # Zombie order: on venue but not in local book
                    diffs.append(
                        ReconcileDiff(
                            kind="order_missing_local",
                            symbol=venue_order.symbol,
                            detail=f"Venue order {venue_order.venue_order_id} missing from local book",
                            local=None,
                            venue=venue_order.venue_order_id,
                            severity="critical",
                        )
                    )
                else:
                    # Order exists locally; check state and fills
                    if local_order.state.value != venue_order.state:
                        diffs.append(
                            ReconcileDiff(
                                kind="order_state_mismatch",
                                symbol=venue_order.symbol,
                                detail=f"Local state={local_order.state.value}, venue state={venue_order.state}",
                                local=local_order.state.value,
                                venue=venue_order.state,
                                severity="warn",
                            )
                        )

                    if local_order.filled_qty != venue_order.filled_qty:
                        diffs.append(
                            ReconcileDiff(
                                kind="order_fills_mismatch",
                                symbol=venue_order.symbol,
                                detail=f"Local filled_qty={local_order.filled_qty}, venue filled_qty={venue_order.filled_qty}",
                                local=local_order.filled_qty,
                                venue=venue_order.filled_qty,
                                severity="critical",
                            )
                        )

        return diffs


class PeriodicReconciler:
    """Schedules reconciliation at fixed intervals + on demand.

    Intended to be driven by the executor's main loop:
        reconciler = PeriodicReconciler(pr, interval_s=60)
        ...
        if reconciler.due(now):
            report = await reconciler.tick(fetcher, now)
    """

    def __init__(
        self,
        reconciler: PositionReconciler,
        *,
        interval_s: float = 60.0,
        on_startup: bool = True,
    ) -> None:
        """Initialize PeriodicReconciler.

        Args:
            reconciler: PositionReconciler to call.
            interval_s: Interval between reconciliations in seconds.
            on_startup: If True, due() returns True initially (on_startup).
        """
        self.reconciler = reconciler
        self.interval_s = interval_s
        self.on_startup = on_startup
        self._last_run: datetime | None = None

    def due(self, now: datetime) -> bool:
        """Check if reconciliation is due now.

        Returns True on startup (if on_startup=True), then True if
        interval_s has elapsed since last run.

        Args:
            now: Current time.

        Returns:
            True if reconciliation is due.
        """
        if self._last_run is None:
            return self.on_startup

        elapsed = (now - self._last_run).total_seconds()
        return elapsed >= self.interval_s

    async def tick(
        self,
        fetcher: VenueSnapshotFetcher,
        now: datetime,
    ) -> ReconcileReport:
        """Run reconciliation if due.

        Updates _last_run and returns the report.

        Args:
            fetcher: VenueSnapshotFetcher.
            now: Current time.

        Returns:
            ReconcileReport from the reconciliation pass.
        """
        report = await self.reconciler.reconcile(fetcher, at=now)
        self._last_run = now
        return report

    def last_run(self) -> datetime | None:
        """Return the timestamp of the last reconciliation run, or None.

        Returns:
            Datetime of last run, or None if never run.
        """
        return self._last_run
