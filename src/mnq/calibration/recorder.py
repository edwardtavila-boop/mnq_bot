"""[REAL] Realized-vs-expected slippage recorder.

Records fill expectations at order submission and matches them with realized fills,
computing slippage ticks and emitting records to the event journal and prometheus.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import polars as pl

from mnq.core.types import Side
from mnq.observability.metrics import fill_slippage_ticks
from mnq.storage.journal import EventJournal
from mnq.storage.schema import (
    FILL_EXPECTED,
    FILL_ORPHANED,
    FILL_REALIZED,
)


@dataclass(frozen=True)
class ExpectedFillContext:
    """Snapshot taken at the moment of order submission."""

    order_id: str
    symbol: str
    side: Side
    qty: int
    submitted_at: datetime
    expected_price: Decimal
    reference_bid: Decimal
    reference_ask: Decimal
    spread_ticks: float
    volatility_regime: str  # "low" | "normal" | "high"
    tod_bucket: str  # "open_5m" | "close_5m" | "rth_body" | "overnight"
    liquidity_proxy: float
    tick_size: Decimal


@dataclass(frozen=True)
class RealizedFillRecord:
    """Joined row: what we expected vs what happened."""

    order_id: str
    expected: ExpectedFillContext
    realized_price: Decimal
    realized_at: datetime
    fill_qty: int
    slippage_ticks: float  # signed; + means worse for us
    latency_ms: float


class SlippageRecorder:
    """Records (expected, realized) pairs for live fills.

    Intended usage from the executor:
        rec = SlippageRecorder(journal=journal)
        rec.record_expected(ctx)          # at order submit
        rec.record_realized(order_id, realized_price, ts, qty)  # on fill
        -> emits a realized fill record to the journal
        -> updates the prometheus histogram

    Pending expectations are buffered in memory until the fill lands.
    """

    def __init__(
        self,
        journal: EventJournal | None = None,
        *,
        timeout_s: float = 300.0,
    ) -> None:
        """Initialize the recorder.

        Args:
            journal: Optional EventJournal for durability.
            timeout_s: Seconds to hold an expectation before garbage collection.
        """
        self.journal = journal
        self.timeout_s = timeout_s
        self._pending: dict[str, ExpectedFillContext] = {}

    def record_expected(self, ctx: ExpectedFillContext) -> None:
        """Record an expected fill at order submission.

        Args:
            ctx: The ExpectedFillContext snapshot.
        """
        self._pending[ctx.order_id] = ctx

        if self.journal is not None:
            payload = {
                "order_id": ctx.order_id,
                "symbol": ctx.symbol,
                "side": ctx.side.value,
                "qty": ctx.qty,
                "expected_price": str(ctx.expected_price),
                "reference_bid": str(ctx.reference_bid),
                "reference_ask": str(ctx.reference_ask),
                "spread_ticks": ctx.spread_ticks,
                "volatility_regime": ctx.volatility_regime,
                "tod_bucket": ctx.tod_bucket,
                "liquidity_proxy": ctx.liquidity_proxy,
                "tick_size": str(ctx.tick_size),
            }
            self.journal.append(FILL_EXPECTED, payload, trace_id=ctx.order_id)

    def record_realized(
        self,
        order_id: str,
        *,
        realized_price: Decimal,
        realized_at: datetime,
        fill_qty: int,
    ) -> RealizedFillRecord | None:
        """Record a realized fill and emit the joined record.

        Args:
            order_id: The order identifier.
            realized_price: The actual fill price.
            realized_at: The fill timestamp.
            fill_qty: The quantity filled.

        Returns:
            The RealizedFillRecord if a matching expectation was found, else None.
        """
        expected = self._pending.pop(order_id, None)
        if expected is None:
            # No matching expectation; emit orphaned event.
            if self.journal is not None:
                payload = {
                    "order_id": order_id,
                    "reason": "no_matching_expectation",
                }
                self.journal.append(
                    FILL_ORPHANED, payload, trace_id=order_id
                )
            return None

        # Compute slippage: positive means worse for us.
        # For LONG: realized > expected is worse (positive slippage).
        # For SHORT: realized < expected is worse (positive slippage).
        if expected.side == Side.LONG:
            slippage_ticks = float((realized_price - expected.expected_price) / expected.tick_size)
        else:  # SHORT
            slippage_ticks = float((expected.expected_price - realized_price) / expected.tick_size)

        latency_ms = (realized_at - expected.submitted_at).total_seconds() * 1000.0

        record = RealizedFillRecord(
            order_id=order_id,
            expected=expected,
            realized_price=realized_price,
            realized_at=realized_at,
            fill_qty=fill_qty,
            slippage_ticks=slippage_ticks,
            latency_ms=latency_ms,
        )

        # Emit to journal.
        if self.journal is not None:
            realized_payload: dict[str, str | float] = {
                "order_id": order_id,
                "realized_price": str(realized_price),
                "slippage_ticks": slippage_ticks,
                "latency_ms": latency_ms,
            }
            realized_payload["fill_qty"] = str(fill_qty)
            self.journal.append(
                FILL_REALIZED, realized_payload, trace_id=order_id
            )

        # Update prometheus histogram.
        fill_slippage_ticks.labels(side=expected.side.value).observe(slippage_ticks)

        return record

    def drop_expired(self, now: datetime) -> list[str]:
        """Garbage-collect expectations that never filled.

        Args:
            now: The current time.

        Returns:
            List of dropped order IDs.
        """
        dropped: list[str] = []
        to_remove: list[str] = []

        for order_id, ctx in self._pending.items():
            age_s = (now - ctx.submitted_at).total_seconds()
            if age_s > self.timeout_s:
                to_remove.append(order_id)
                dropped.append(order_id)

                # Emit orphaned event.
                if self.journal is not None:
                    payload = {
                        "order_id": order_id,
                        "reason": "timeout",
                    }
                    self.journal.append(
                        FILL_ORPHANED, payload, trace_id=order_id
                    )

        for order_id in to_remove:
            del self._pending[order_id]

        return dropped

    def pending_count(self) -> int:
        """Return the number of pending expectations."""
        return len(self._pending)


def export_to_dataframe(journal: EventJournal) -> pl.DataFrame:
    """Replay FILL_REALIZED events and return a polars DataFrame.

    Suitable for feeding into mnq.calibration.fit_slippage.fit_per_regime(...).

    Args:
        journal: The EventJournal to replay.

    Returns:
        A polars DataFrame with columns:
            - order_id, side, expected_price, realized_price, slippage_ticks,
              tod_bucket, volatility_regime, liquidity_proxy, bar_atr_ticks,
              session_phase_minute, bar_volume (as nulls if not available).
    """
    from mnq.storage.schema import FILL_EXPECTED, FILL_REALIZED

    # Collect fill_expected and fill_realized events.
    expected_by_id: dict[str, dict[str, object]] = {}
    realized_records: list[dict[str, object]] = []

    for entry in journal.replay(event_types=(FILL_EXPECTED, FILL_REALIZED)):
        if entry.event_type == FILL_EXPECTED:
            expected_by_id[entry.payload["order_id"]] = entry.payload
        elif entry.event_type == FILL_REALIZED:
            order_id = entry.payload["order_id"]
            expected = expected_by_id.get(order_id)
            if expected is not None:
                # Join expected and realized.
                def to_float(val: object, default: float = 0.0) -> float:
                    try:
                        return float(val)  # type: ignore[arg-type]
                    except (ValueError, TypeError):
                        return default

                exp_price_float = to_float(expected.get("expected_price"))
                real_price_float = to_float(entry.payload.get("realized_price"))
                row: dict[str, object] = {
                    "order_id": order_id,
                    "side": expected.get("side"),
                    "expected_price": exp_price_float,
                    "realized_price": real_price_float,
                    "slippage_ticks": entry.payload.get("slippage_ticks"),
                    "tod_bucket": expected.get("tod_bucket"),
                    "volatility_regime": expected.get("volatility_regime"),
                    "liquidity_proxy": expected.get("liquidity_proxy"),
                    "bar_atr_ticks": None,  # Not captured at order time
                    "session_phase_minute": None,
                    "bar_volume": None,
                }
                realized_records.append(row)

    if not realized_records:
        # Return empty dataframe with correct schema.
        return pl.DataFrame(
            {
                "order_id": [],
                "side": [],
                "expected_price": [],
                "realized_price": [],
                "slippage_ticks": [],
                "tod_bucket": [],
                "volatility_regime": [],
                "liquidity_proxy": [],
                "bar_atr_ticks": [],
                "session_phase_minute": [],
                "bar_volume": [],
            }
        )

    return pl.DataFrame(realized_records)
