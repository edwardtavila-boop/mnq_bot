"""Order flow analysis for the gauntlet — Bookmap/DOM-derived features.

Batch 6C. Computes per-bar order flow features from OHLCV data (sim mode)
or live DOM/tick data (live mode). These features feed into the gauntlet's
``gate_orderflow`` and the ``VolumeAwareSlippage`` model.

Sim-mode features (computable from bars alone):
  - ``cvd``: Cumulative Volume Delta (running sum of bar delta)
  - ``bar_delta``: Single-bar volume delta (positive = buyers, negative = sellers)
  - ``imbalance``: Bid/ask depth imbalance estimate (-1..+1)
  - ``absorption_score``: Range/body ratio as absorption proxy (0..1)
  - ``buy_aggressor_pct``: Estimated percentage of volume from aggressive buyers

Live-mode features (when DOM/tick data is available):
  - ``bid_depth``: Sum of top-N bid levels
  - ``ask_depth``: Sum of top-N ask levels
  - ``ofi``: Order Flow Imbalance (depth change momentum)

The sim-mode close-to-range method approximates delta with ~65% accuracy
compared to actual tick data — sufficient for filtering, not for prediction.

Bookmap integration notes:
  - Bookmap heatmap snapshots map to ``DepthSnapshot`` (top N levels)
  - Absorption detection maps to high ``range_body_ratio`` + ``imbalance``
  - Spoofing detection requires consecutive DOM snapshots (live only)

References:
  - Original: ``mnq_apex_bot/order_flow.py`` (Tradovate live WS)
  - Original: ``mnq_apex_bot/microstructure.py`` (feature layer)
  - Superpowers Module 9: ``dom_orderflow.md``
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from mnq.core.types import Bar


@dataclass(frozen=True, slots=True)
class OrderFlowSnapshot:
    """Per-bar order flow state — sim or live."""

    bar_delta: float  # single-bar volume delta
    cvd: float  # cumulative volume delta (session running sum)
    imbalance: float  # bid/ask imbalance estimate [-1, +1]
    absorption_score: float  # range/body ratio proxy [0, 1]
    buy_aggressor_pct: float  # estimated buyer aggression [0, 1]
    bid_depth: float  # top-of-book bid depth (0 in sim)
    ask_depth: float  # top-of-book ask depth (0 in sim)
    is_live: bool  # True if computed from real DOM data


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    """Top-of-book depth from Bookmap/DOM — mirrors heatmap data.

    Each level is (price, size). Sorted best→worst.
    """

    bids: list[tuple[float, int]]  # [(price, size), ...]
    asks: list[tuple[float, int]]
    timestamp_ms: int = 0

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def spread_ticks(self) -> float:
        """Spread in ticks (0.25 = 1 tick for MNQ)."""
        if not self.bids or not self.asks:
            return 0.0
        return (self.best_ask - self.best_bid) / 0.25

    @property
    def total_bid_depth(self) -> int:
        return sum(s for _, s in self.bids)

    @property
    def total_ask_depth(self) -> int:
        return sum(s for _, s in self.asks)

    @property
    def imbalance(self) -> float:
        """Depth imbalance: +1 = all bids, -1 = all asks."""
        total = self.total_bid_depth + self.total_ask_depth
        if total == 0:
            return 0.0
        return (self.total_bid_depth - self.total_ask_depth) / total


class OrderFlowTracker:
    """Stateful order flow tracker — accumulates bar-by-bar.

    Sim mode: call ``on_bar()`` per bar → get OrderFlowSnapshot.
    Live mode: call ``on_dom_update()`` / ``on_tick()`` then ``on_bar()``.
    """

    def __init__(self, *, cvd_window: int = 5, dom_depth: int = 10) -> None:
        self._cvd: float = 0.0
        self._cvd_window = cvd_window
        self._dom_depth = dom_depth
        self._delta_hist: deque[float] = deque(maxlen=cvd_window)

        # Live DOM state (populated via on_dom_update)
        self._best_bid: float = 0.0
        self._best_ask: float = 0.0
        self._bid_depth: float = 0.0
        self._ask_depth: float = 0.0
        self._live_delta: float = 0.0
        self._has_live_data = False

    def on_bar(self, bar: Bar) -> OrderFlowSnapshot:
        """Process a bar and return the current order flow state.

        In sim mode, estimates delta from OHLCV using the close-to-range method.
        In live mode, uses accumulated tick delta from ``on_tick()``.
        """
        o, h, lo, c = float(bar.open), float(bar.high), float(bar.low), float(bar.close)
        vol = float(bar.volume)

        # Delta estimation
        if self._has_live_data:
            bar_delta = self._live_delta
            self._live_delta = 0.0
        else:
            bar_range = h - lo
            if bar_range > 0 and vol > 0:
                close_pct = (c - lo) / bar_range
                bar_delta = vol * (2 * close_pct - 1)
            else:
                bar_delta = 0.0

        self._delta_hist.append(bar_delta)
        self._cvd += bar_delta

        # Imbalance estimate
        if self._has_live_data:
            total_depth = self._bid_depth + self._ask_depth
            imbalance = (
                (self._bid_depth - self._ask_depth) / total_depth if total_depth > 0 else 0.0
            )
        else:
            # Proxy from bar: bullish close → positive imbalance
            bar_range = h - lo
            imbalance = (2 * (c - lo) / bar_range - 1) if bar_range > 0 else 0.0

        # Absorption score: high range + small body = absorption
        body = abs(c - o)
        bar_range = h - lo
        if bar_range > 0:
            body_ratio = body / bar_range
            absorption_score = max(0.0, min(1.0, 1.0 - body_ratio))
        else:
            absorption_score = 0.0

        # Buy aggressor percentage
        buy_aggressor_pct = (c - lo) / bar_range if vol > 0 and bar_range > 0 else 0.5

        return OrderFlowSnapshot(
            bar_delta=round(bar_delta, 2),
            cvd=round(self._cvd, 2),
            imbalance=round(max(-1.0, min(1.0, imbalance)), 4),
            absorption_score=round(absorption_score, 4),
            buy_aggressor_pct=round(max(0.0, min(1.0, buy_aggressor_pct)), 4),
            bid_depth=self._bid_depth,
            ask_depth=self._ask_depth,
            is_live=self._has_live_data,
        )

    def on_dom_update(self, snapshot: DepthSnapshot) -> None:
        """Ingest a Bookmap/DOM depth snapshot (live mode)."""
        self._has_live_data = True
        self._bid_depth = float(snapshot.total_bid_depth)
        self._ask_depth = float(snapshot.total_ask_depth)
        if snapshot.bids:
            self._best_bid = snapshot.best_bid
        if snapshot.asks:
            self._best_ask = snapshot.best_ask

    def on_tick(self, price: float, size: int, is_buy: bool) -> None:
        """Ingest a trade tick (live mode)."""
        self._has_live_data = True
        if is_buy:
            self._live_delta += size
        else:
            self._live_delta -= size

    def reset_session(self) -> None:
        """Reset CVD and delta history (new session)."""
        self._cvd = 0.0
        self._delta_hist.clear()
        self._live_delta = 0.0

    @property
    def cvd(self) -> float:
        return self._cvd

    @property
    def cvd_slope(self) -> float:
        """5-bar slope of CVD. Positive = accelerating buying."""
        if len(self._delta_hist) < 2:
            return 0.0
        deltas = list(self._delta_hist)
        n = len(deltas)
        # Simple linear regression slope
        x_mean = (n - 1) / 2
        y_mean = sum(deltas) / n
        numer = sum((i - x_mean) * (d - y_mean) for i, d in enumerate(deltas))
        denom = sum((i - x_mean) ** 2 for i in range(n))
        return numer / denom if denom > 0 else 0.0


def orderflow_from_bars(
    bars: list[Bar],
    *,
    eval_bar_idx: int | None = None,
) -> OrderFlowSnapshot:
    """Compute order flow snapshot at a specific bar index.

    Runs the tracker through all bars up to eval_bar_idx and returns
    the snapshot at that bar. Used by the gauntlet's gate_orderflow.
    """
    if not bars:
        return OrderFlowSnapshot(
            bar_delta=0.0,
            cvd=0.0,
            imbalance=0.0,
            absorption_score=0.0,
            buy_aggressor_pct=0.5,
            bid_depth=0.0,
            ask_depth=0.0,
            is_live=False,
        )

    if eval_bar_idx is None:
        eval_bar_idx = len(bars) - 1
    eval_bar_idx = min(eval_bar_idx, len(bars) - 1)

    tracker = OrderFlowTracker()
    snapshot = None
    for i, bar in enumerate(bars):
        snapshot = tracker.on_bar(bar)
        if i >= eval_bar_idx:
            break

    return snapshot  # type: ignore[return-value]


def depth_aware_slippage_ticks(
    qty: int,
    depth: DepthSnapshot | None,
    *,
    base_ticks: float = 0.5,
    default_depth: int = 50,
) -> float:
    """Estimate slippage ticks given order size and DOM depth.

    When a DepthSnapshot is available (Bookmap/live), walks the book
    to estimate how many levels the order would consume.
    When no depth is available, falls back to the VolumeAwareSlippage formula.
    """
    if depth is None:
        # Fallback: simple qty/depth ratio
        ratio = qty / default_depth if default_depth > 0 else 0.0
        return base_ticks + ratio**0.5

    # Walk the book: accumulate size at each level until qty is filled
    book = depth.bids  # for a buy; for sell, use asks
    remaining = qty
    levels_consumed = 0
    for _price, size in book:
        if remaining <= 0:
            break
        consumed = min(remaining, size)
        remaining -= consumed
        levels_consumed += 1

    # If we walked through all levels and still have remaining, that's max impact
    if remaining > 0:
        levels_consumed += remaining / max(1, default_depth // 10)

    return base_ticks + levels_consumed * 0.5


__all__ = [
    "OrderFlowSnapshot",
    "OrderFlowTracker",
    "DepthSnapshot",
    "orderflow_from_bars",
    "depth_aware_slippage_ticks",
]
