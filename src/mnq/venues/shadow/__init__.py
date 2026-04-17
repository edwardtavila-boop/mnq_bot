"""Shadow venue — simulate-in-real-time, never route real orders.

Batch 4A scaffold + Batch 4B realism.

The shadow venue sits between the orchestrator's filtered+apex-gated
signal stream and a future real broker connection. It accepts Signals
exactly like ``TradovateRest.place_order`` but instead of emitting a
live order, it:

  1. Stamps each signal with a deterministic shadow order id.
  2. Applies a configurable **slippage model** to shift the fill price
     adversely (toward unfavorable direction), reflecting real-world
     market impact.
  3. Applies a configurable **latency model** to shift the fill timestamp
     forward, simulating gateway-to-exchange round-trip time.
  4. Records a ``Fill`` tagged with ``venue="shadow"`` and appends it to
     ``data/shadow/fills.jsonl`` so parity tooling
     (``src/mnq/observability/parity.py``) can diff shadow vs paper-sim.

The venue is **deterministic** — given the same (bar, signal) inputs in
the same order *and the same RNG seed*, it produces identical output.
That's the contract parity wants.

Usage (MVP — zero slippage, zero latency, same as 4A):

    venue = ShadowVenue(journal_path=Path("data/shadow/fills.jsonl"))
    result = venue.place_order(signal, at_price=bar.close, at_ts=bar.ts)

Usage (realistic — slippage + latency):

    venue = ShadowVenue(
        journal_path=Path("data/shadow/fills.jsonl"),
        slippage=FixedTickSlippage(ticks=1),
        latency=FixedLatency(ms=50),
        max_position_qty=4,
    )
    result = venue.place_order(signal, at_price=bar.close, at_ts=bar.ts)
    if result.rejected:
        print(result.reject_reason)

Partial fills are modeled via ``PartialFillModel``: qty may be reduced
stochastically. ``fill.is_partial`` flags when this happens.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from mnq.core.types import Fill, Side, Signal

# =====================================================================
# Slippage models
# =====================================================================

class SlippageProvider(Protocol):
    """Interface for slippage models.  Returns adverse ticks to add."""

    def ticks(self, side: Side, price: Decimal, qty: int) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class ZeroSlippage:
    """4A default — no slippage."""

    def ticks(self, side: Side, price: Decimal, qty: int) -> Decimal:
        return Decimal(0)


@dataclass(frozen=True, slots=True)
class FixedTickSlippage:
    """Constant adverse slippage in ticks. 1 tick = 0.25 for MNQ."""

    tick_count: int = 1
    tick_size: Decimal = Decimal("0.25")

    def ticks(self, side: Side, price: Decimal, qty: int) -> Decimal:
        return self.tick_size * self.tick_count


@dataclass(frozen=True, slots=True)
class StochasticSlippage:
    """Mean + std adverse slippage (in ticks), clamped to [0, max].

    Uses the venue's seeded RNG for determinism.
    """

    mean_ticks: float = 0.8
    std_ticks: float = 0.4
    max_ticks: int = 4
    tick_size: Decimal = Decimal("0.25")
    _rng: random.Random = field(default_factory=lambda: random.Random(42))

    def ticks(self, side: Side, price: Decimal, qty: int) -> Decimal:
        raw = self._rng.gauss(self.mean_ticks, self.std_ticks)
        clamped = max(0.0, min(float(self.max_ticks), raw))
        # Round to nearest whole tick
        n = round(clamped)
        return self.tick_size * n


@dataclass(frozen=True, slots=True)
class VolumeAwareSlippage:
    """Slippage that scales with order qty relative to typical book depth.

    Batch 6C. Models the observation that larger orders walk the book
    further, while small (1-lot) orders typically get minimal slippage.

    Formula: base_ticks + scale_factor * (qty / depth_lots)^elasticity

    With defaults: 1-lot @ 50-lot depth → ~0.6 ticks
                   5-lot @ 50-lot depth → ~1.2 ticks
                   10-lot @ 50-lot depth → ~1.7 ticks
    """

    base_ticks: float = 0.5
    scale_factor: float = 1.0
    depth_lots: int = 50  # typical top-of-book depth for MNQ
    elasticity: float = 0.5  # sub-linear: doubling qty doesn't double slip
    max_ticks: int = 8
    tick_size: Decimal = Decimal("0.25")
    _rng: random.Random = field(default_factory=lambda: random.Random(45))

    def ticks(self, side: Side, price: Decimal, qty: int) -> Decimal:
        ratio = qty / self.depth_lots if self.depth_lots > 0 else 0.0
        raw = self.base_ticks + self.scale_factor * (ratio ** self.elasticity)
        # Add small noise
        noise = self._rng.gauss(0, 0.15)
        raw = max(0.0, min(float(self.max_ticks), raw + noise))
        n = round(raw)
        return self.tick_size * n


# =====================================================================
# Latency models
# =====================================================================

class LatencyProvider(Protocol):
    """Interface for latency models.  Returns a timedelta to add to fill ts."""

    def delay(self) -> timedelta: ...


@dataclass(frozen=True, slots=True)
class ZeroLatency:
    """4A default — no latency."""

    def delay(self) -> timedelta:
        return timedelta(0)


@dataclass(frozen=True, slots=True)
class FixedLatency:
    """Constant round-trip latency in milliseconds."""

    ms: int = 50

    def delay(self) -> timedelta:
        return timedelta(milliseconds=self.ms)


@dataclass(frozen=True, slots=True)
class StochasticLatency:
    """Log-normal latency model.  Mean + std in milliseconds."""

    mean_ms: float = 45.0
    std_ms: float = 15.0
    max_ms: float = 500.0
    _rng: random.Random = field(default_factory=lambda: random.Random(43))

    def delay(self) -> timedelta:
        # Log-normal: convert mean/std of underlying normal
        mu = math.log(self.mean_ms**2 / math.sqrt(self.std_ms**2 + self.mean_ms**2))
        sigma = math.sqrt(math.log(1 + (self.std_ms / self.mean_ms) ** 2))
        raw = self._rng.lognormvariate(mu, sigma)
        clamped = min(raw, self.max_ms)
        return timedelta(milliseconds=clamped)


# =====================================================================
# Partial-fill model
# =====================================================================

class PartialFillProvider(Protocol):
    """Interface for partial-fill models.  Returns filled qty <= requested."""

    def filled_qty(self, requested_qty: int) -> int: ...


@dataclass(frozen=True, slots=True)
class FullFill:
    """4A default — always fills entire qty."""

    def filled_qty(self, requested_qty: int) -> int:
        return requested_qty


@dataclass(frozen=True, slots=True)
class StochasticPartialFill:
    """Randomly reduces fill qty with configurable probability.

    With probability ``partial_prob``, fills a random fraction [min_fill_pct, 1.0)
    of the requested qty (floored to at least 1).
    """

    partial_prob: float = 0.05
    min_fill_pct: float = 0.5
    _rng: random.Random = field(default_factory=lambda: random.Random(44))

    def filled_qty(self, requested_qty: int) -> int:
        if requested_qty <= 1 or self._rng.random() >= self.partial_prob:
            return requested_qty
        pct = self._rng.uniform(self.min_fill_pct, 1.0)
        return max(1, int(requested_qty * pct))


# =====================================================================
# Order result
# =====================================================================


@dataclass(frozen=True, slots=True)
class ShadowOrderResult:
    """What ShadowVenue.place_order returns — fill + metadata."""

    fill: Fill
    rejected: bool = False
    reject_reason: str = ""
    slippage_ticks: Decimal = Decimal(0)
    latency_ms: float = 0.0
    requested_qty: int = 0


# =====================================================================
# ShadowVenue
# =====================================================================


class ShadowVenue:
    """In-process shadow venue. Writes fills to an append-only JSONL journal.

    Batch 4A: deterministic, zero-slippage, zero-latency, never-reject.
    Batch 4B: configurable slippage, latency, partial fills, position-limit
              rejection.
    """

    VENUE_TAG = "shadow"

    def __init__(
        self,
        *,
        journal_path: Path | str | None = None,
        commission_per_side: Decimal = Decimal("0.85"),
        seed_prefix: str = "shadow",
        slippage: SlippageProvider | None = None,
        latency: LatencyProvider | None = None,
        partial_fill: PartialFillProvider | None = None,
        max_position_qty: int | None = None,
    ) -> None:
        self._commission = commission_per_side
        self._prefix = seed_prefix
        self._counter = 0
        self._fills: list[Fill] = []
        self._rejections: list[ShadowOrderResult] = []
        self._journal_path: Path | None = Path(journal_path) if journal_path else None
        self._writer: Any = None
        # Realism providers — default to 4A behavior (zero everything)
        self._slippage: SlippageProvider = slippage or ZeroSlippage()
        self._latency: LatencyProvider = latency or ZeroLatency()
        self._partial_fill: PartialFillProvider = partial_fill or FullFill()
        self._max_position_qty = max_position_qty
        # Net position tracker for rejection logic
        self._net_qty = 0  # signed: +long, -short
        if self._journal_path is not None:
            self._journal_path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = self._journal_path.open("a", encoding="utf-8")

    # -----------------------------------------------------------------
    # Core API
    # -----------------------------------------------------------------

    def place_order(
        self,
        signal: Signal,
        *,
        at_price: Decimal,
        at_ts: datetime,
    ) -> ShadowOrderResult:
        """Record a simulated fill with slippage, latency, and rejection.

        Rejection conditions (Batch 4B):
          - ``max_position_qty`` exceeded: adding to the same-direction
            position would breach the limit.

        Slippage:
          - Adverse price shift via the configured SlippageProvider.
          - Long orders pay *more*; short orders pay *less*.

        Latency:
          - Fill timestamp shifted forward by the configured LatencyProvider.

        Partial fills:
          - Qty may be reduced by the configured PartialFillProvider.
        """
        self._counter += 1
        order_id = f"{self._prefix}-{self._counter:06d}"

        # --- Rejection check ---
        if self._max_position_qty is not None:
            proposed_delta = signal.qty if signal.side == Side.LONG else -signal.qty
            proposed_position = self._net_qty + proposed_delta
            if abs(proposed_position) > self._max_position_qty:
                rejection = ShadowOrderResult(
                    fill=Fill(
                        order_id=order_id,
                        spec_hash=signal.spec_hash,
                        ts=at_ts,
                        side=signal.side,
                        qty=0,
                        price=at_price,
                        commission=Decimal(0),
                        venue=self.VENUE_TAG,
                        venue_fill_id=f"{order_id}-R",
                        is_partial=False,
                    ),
                    rejected=True,
                    reject_reason=(
                        f"position_limit: net_qty={self._net_qty}, "
                        f"proposed={proposed_position}, max={self._max_position_qty}"
                    ),
                    requested_qty=signal.qty,
                )
                self._rejections.append(rejection)
                return rejection

        # --- Partial fill ---
        filled_qty = self._partial_fill.filled_qty(signal.qty)
        is_partial = filled_qty < signal.qty

        # --- Slippage (adverse: long pays more, short receives less) ---
        slip_ticks = self._slippage.ticks(signal.side, at_price, filled_qty)
        fill_price = at_price + slip_ticks if signal.side == Side.LONG else at_price - slip_ticks

        # --- Latency ---
        delay = self._latency.delay()
        fill_ts = at_ts + delay

        fill = Fill(
            order_id=order_id,
            spec_hash=signal.spec_hash,
            ts=fill_ts,
            side=signal.side,
            qty=filled_qty,
            price=fill_price,
            commission=self._commission,
            venue=self.VENUE_TAG,
            venue_fill_id=f"{order_id}-F",
            is_partial=is_partial,
        )
        self._fills.append(fill)

        # Update net position
        delta = filled_qty if signal.side == Side.LONG else -filled_qty
        self._net_qty += delta

        self._write_jsonl(fill)
        return ShadowOrderResult(
            fill=fill,
            slippage_ticks=slip_ticks,
            latency_ms=delay.total_seconds() * 1000,
            requested_qty=signal.qty,
        )

    def get_fills(self) -> list[Fill]:
        """Return all fills written in this venue instance (not the whole journal)."""
        return list(self._fills)

    def get_rejections(self) -> list[ShadowOrderResult]:
        """Return all rejected order attempts."""
        return list(self._rejections)

    @property
    def net_qty(self) -> int:
        """Current net position (signed)."""
        return self._net_qty

    @property
    def n_rejected(self) -> int:
        return len(self._rejections)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.flush()
            self._writer.close()
            self._writer = None

    def __enter__(self) -> ShadowVenue:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _write_jsonl(self, fill: Fill) -> None:
        if self._writer is None:
            return
        rec = {
            "order_id": fill.order_id,
            "spec_hash": fill.spec_hash,
            "ts": fill.ts.isoformat(),
            "side": fill.side.value if isinstance(fill.side, Side) else str(fill.side),
            "qty": fill.qty,
            "price": str(fill.price),
            "commission": str(fill.commission),
            "venue": fill.venue,
            "venue_fill_id": fill.venue_fill_id,
            "is_partial": fill.is_partial,
        }
        self._writer.write(json.dumps(rec) + "\n")


__all__ = [
    "ShadowVenue",
    "ShadowOrderResult",
    "ZeroSlippage",
    "FixedTickSlippage",
    "StochasticSlippage",
    "ZeroLatency",
    "FixedLatency",
    "StochasticLatency",
    "FullFill",
    "StochasticPartialFill",
    "VolumeAwareSlippage",
]
