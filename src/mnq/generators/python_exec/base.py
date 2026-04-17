"""[REAL] Base class for generated Python executor strategies.

Generated subclasses implement:
    _eval_long(ctx: BarCtx)  -> bool
    _eval_short(ctx: BarCtx) -> bool
    _compute_stop_ticks(ctx: BarCtx) -> int

This base class owns the mechanical bits shared by every strategy:

- Ingest `Bar`s one at a time via `on_bar`.
- Update every feature instance.
- Maintain a small history ring per feature for lookback operators
  (`[N]` offset, `for_bars`, `within_bars`, `rising`, `falling`).
- Maintain book-keeping: `bars_since_entry`, `bars_since_session_open`,
  `position_size`, `session_window`, `in_blackout`.
- Emit zero or one `Signal` per bar close.

Risk-manager integration is deliberately hook-shaped, not owned here —
the executor wraps `StrategyBase` and enforces the spec's risk caps
before any signal reaches the venue.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

from mnq.core.types import (
    Bar,
    OrderType,
    Side,
    Signal,
    quantize_to_tick,
)
from mnq.spec.schema import StrategySpec


class HistoryRing:
    """Bounded ring buffer for a single feature's recent values.

    Index 0 is the most recent value; index N is N bars ago. Raises if
    asked for a position not yet populated.
    """

    __slots__ = ("_buf", "_capacity")

    def __init__(self, capacity: int = 32) -> None:
        self._buf: deque[float | None] = deque(maxlen=capacity)
        self._capacity = capacity

    def push(self, v: float | None) -> None:
        self._buf.appendleft(v)

    def __getitem__(self, i: int) -> float | None:
        if i < 0 or i >= len(self._buf):
            return None
        return self._buf[i]

    def __len__(self) -> int:
        return len(self._buf)

    def rising(self, n: int) -> bool:
        """True if value has been strictly rising over last n bars."""
        if len(self._buf) <= n:
            return False
        for i in range(n):
            a, b = self._buf[i], self._buf[i + 1]
            if a is None or b is None or not (a > b):
                return False
        return True

    def falling(self, n: int) -> bool:
        if len(self._buf) <= n:
            return False
        for i in range(n):
            a, b = self._buf[i], self._buf[i + 1]
            if a is None or b is None or not (a < b):
                return False
        return True

    def crossed_above(self, other: HistoryRing, within_bars: int) -> bool:
        """True if self crossed above other within the last `within_bars` bars."""
        if within_bars < 1:
            return False
        if len(self) < 2 or len(other) < 2:
            return False
        for i in range(min(within_bars, len(self) - 1, len(other) - 1)):
            a_now, a_prev = self._buf[i], self._buf[i + 1]
            b_now, b_prev = other._buf[i], other._buf[i + 1]
            if a_now is None or a_prev is None or b_now is None or b_prev is None:
                continue
            if a_prev <= b_prev and a_now > b_now:
                return True
        return False

    def crossed_below(self, other: HistoryRing, within_bars: int) -> bool:
        if within_bars < 1:
            return False
        if len(self) < 2 or len(other) < 2:
            return False
        for i in range(min(within_bars, len(self) - 1, len(other) - 1)):
            a_now, a_prev = self._buf[i], self._buf[i + 1]
            b_now, b_prev = other._buf[i], other._buf[i + 1]
            if a_now is None or a_prev is None or b_now is None or b_prev is None:
                continue
            if a_prev >= b_prev and a_now < b_now:
                return True
        return False


@dataclass
class BarCtx:
    """Frozen view of the world at a single bar close, passed to eval methods."""

    bar: Bar
    features: dict[str, float | None]
    history: dict[str, HistoryRing]
    position_size: int
    bars_since_entry: int
    bars_since_session_open: int
    session_window: str | None
    in_blackout: bool
    close_prev: float | None

    def f(self, name: str) -> float:
        """Raise if the named feature isn't ready yet."""
        v = self.features.get(name)
        if v is None:
            raise RuntimeError(f"feature {name!r} not ready")
        return v

    def hist(self, name: str) -> HistoryRing:
        return self.history[name]


# ---- session/blackout helpers ----


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class _SessionWindow:
    name: str
    start: time
    end: time
    enabled: bool


@dataclass
class _Blackout:
    name: str
    kind: str  # session_offset | economic_event
    start_sec_from_open: int | None = None
    end_sec_from_open: int | None = None
    end_sec_from_close: int | None = None
    duration_sec: int | None = None
    event: str | None = None


# ---- base class ----


class StrategyBase:
    """Common machinery for generated strategies.

    A generated subclass provides `spec`, `_features_factory`, and the
    three hook methods. The subclass's `__init__` calls `super().__init__`.
    """

    def __init__(
        self,
        spec: StrategySpec,
        features: dict[str, Any],
        *,
        history_capacity: int = 32,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.spec = spec
        self.features = features
        self._history: dict[str, HistoryRing] = {
            fid: HistoryRing(history_capacity) for fid in features
        }
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

        # State
        self._position_size: int = 0
        self._entry_bar_index: int | None = None
        self._bar_index: int = -1
        self._last_session_day: str | None = None
        self._session_open_bar: int | None = None
        self._prev_close: float | None = None

        # Session + blackout precompute
        self._windows: list[_SessionWindow] = [
            _SessionWindow(
                name=w.name,
                start=_parse_hhmm(w.start),
                end=_parse_hhmm(w.end),
                enabled=w.enabled,
            )
            for w in spec.session.windows
        ]
        self._blackouts: list[_Blackout] = [
            _Blackout(
                name=b.name,
                kind=b.type,
                start_sec_from_open=b.offset_from_session_start_sec,
                end_sec_from_close=b.offset_from_session_end_sec,
                duration_sec=b.duration_sec,
                event=b.event,
            )
            for b in spec.session.blackouts
        ]
        self._session_tz_name = spec.session.timezone

        # Tick math
        self._tick = spec.instrument.tick_size

    # ---- externally-driven state updates ----

    def update_position(self, signed_qty: int) -> None:
        """Called by the executor after a fill lands."""
        prev = self._position_size
        self._position_size = signed_qty
        if prev == 0 and signed_qty != 0:
            self._entry_bar_index = self._bar_index
        elif signed_qty == 0:
            self._entry_bar_index = None

    # ---- per-bar entry point ----

    def on_bar(self, bar: Bar) -> Signal | None:
        """Update features, maintain session state, return a Signal or None."""
        self._bar_index += 1

        # Feature updates + history push
        for fid, feat in self.features.items():
            val = feat.update(bar)
            self._history[fid].push(val)

        # Session window + bars_since_session_open bookkeeping
        sess_day_key = self._session_day_key(bar.ts)
        if sess_day_key != self._last_session_day:
            self._last_session_day = sess_day_key
            self._session_open_bar = self._bar_index
        bars_since_open = self._bar_index - (self._session_open_bar or self._bar_index)
        window_name = self._current_window(bar.ts)
        in_bk = self._in_blackout(bar.ts, bars_since_open)

        ctx = BarCtx(
            bar=bar,
            features={fid: f.value for fid, f in self.features.items()},
            history=self._history,
            position_size=self._position_size,
            bars_since_entry=(self._bar_index - (self._entry_bar_index or self._bar_index))
            if self._entry_bar_index is not None
            else 0,
            bars_since_session_open=bars_since_open,
            session_window=window_name,
            in_blackout=in_bk,
            close_prev=self._prev_close,
        )

        # Only consider entries when flat (enforced by risk anyway, but cheap here).
        signal = None
        if self._position_size == 0 and all(f.ready for f in self.features.values()):
            if self._eval_long(ctx):
                signal = self._build_signal(Side.LONG, ctx)
            elif self._eval_short(ctx):
                signal = self._build_signal(Side.SHORT, ctx)

        self._prev_close = float(bar.close)
        return signal

    # ---- helpers used by subclass ----

    def _build_signal(self, side: Side, ctx: BarCtx) -> Signal:
        stop_ticks = int(self._compute_stop_ticks(ctx))
        stop_ticks = max(self.spec.exit.initial_stop.min_ticks,
                         min(self.spec.exit.initial_stop.max_ticks, stop_ticks))
        tp_ticks = self._compute_tp_ticks(stop_ticks)

        ref = quantize_to_tick(ctx.bar.close, self._tick)
        tick = self._tick
        if side is Side.LONG:
            stop_px = quantize_to_tick(ref - tick * stop_ticks, tick)
            tp_px = quantize_to_tick(ref + tick * tp_ticks, tick)
        else:
            stop_px = quantize_to_tick(ref + tick * stop_ticks, tick)
            tp_px = quantize_to_tick(ref - tick * tp_ticks, tick)

        ot = _map_order_type(self.spec.execution.order_type)
        qty = self._size_contracts(stop_ticks)

        return Signal(
            side=side,
            qty=qty,
            ref_price=ref,
            stop=stop_px,
            take_profit=tp_px,
            order_type=ot,
            limit_offset_ticks=int(self.spec.execution.limit_offset_ticks),
            market_fallback_ms=int(self.spec.execution.market_fallback_ms),
            time_stop_bars=int(self.spec.exit.time_stop_bars or 0),
            breakeven_at_r=(
                self.spec.exit.breakeven.activate_at_r
                if self.spec.exit.breakeven is not None
                else None
            ),
            trail=(
                {
                    "type": self.spec.exit.trailing.type,
                    "activate_at_r": str(self.spec.exit.trailing.activate_at_r),
                    "giveback_fraction": str(self.spec.exit.trailing.giveback_fraction)
                    if self.spec.exit.trailing.giveback_fraction is not None
                    else None,
                }
                if self.spec.exit.trailing is not None
                else None
            ),
            spec_hash=self.spec.strategy.content_hash,
            spec_semver=self.spec.strategy.semver,
        )

    def _compute_tp_ticks(self, stop_ticks: int) -> int:
        tp = self.spec.exit.take_profit
        if tp.type == "r_multiple":
            mult = float(tp.value) if tp.value is not None else 1.0
            return max(1, int(round(stop_ticks * mult)))
        if tp.type == "fixed_ticks":
            return max(1, int(tp.value or 0))
        if tp.type == "atr_multiple":
            # delegate to subclass override via _compute_stop_ticks-like mechanism:
            # for now we assume generator inlined it. Fall back to r=1.5 * stop.
            return max(1, int(round(stop_ticks * 1.5)))
        raise ValueError(f"unknown take_profit.type: {tp.type!r}")  # pragma: no cover

    def _size_contracts(self, stop_ticks: int) -> int:
        ps = self.spec.position_sizing
        if ps.mode == "fixed_contracts":
            qty = int(ps.fixed_contracts or 1)
        else:
            risk_usd = float(ps.risk_per_trade_usd or 0)
            tick_value = float(self.spec.instrument.tick_size) * float(self.spec.instrument.point_value)
            per_ct_risk = stop_ticks * tick_value
            if per_ct_risk <= 0:
                qty = int(ps.min_contracts)
            else:
                raw = risk_usd / per_ct_risk
                if ps.rounding == "floor":
                    qty = int(raw)
                elif ps.rounding == "ceil":
                    qty = int(raw) + (1 if raw > int(raw) else 0)
                else:
                    qty = int(round(raw))
        qty = max(int(ps.min_contracts), min(int(ps.max_contracts), qty))
        return qty

    def _session_day_key(self, ts: datetime) -> str:
        return ts.date().isoformat()

    def _current_window(self, ts: datetime) -> str | None:
        t = ts.time()
        for w in self._windows:
            if not w.enabled:
                continue
            if w.start <= t < w.end:
                return w.name
        return None

    def _in_blackout(self, ts: datetime, bars_since_open: int) -> bool:
        for b in self._blackouts:
            if b.kind == "session_offset":
                if b.start_sec_from_open is not None and b.duration_sec is not None:
                    start_bars = max(0, b.start_sec_from_open // 60)
                    end_bars = start_bars + max(1, (b.duration_sec + 59) // 60)
                    if start_bars <= bars_since_open < end_bars:
                        return True
                # end-anchored handled by executor wall-clock; base class returns False.
            # economic-event blackouts are executor-enforced.
        return False

    # ---- hooks (overridden by generated subclass) ----

    def _eval_long(self, ctx: BarCtx) -> bool:   # pragma: no cover
        raise NotImplementedError

    def _eval_short(self, ctx: BarCtx) -> bool:  # pragma: no cover
        raise NotImplementedError

    def _compute_stop_ticks(self, ctx: BarCtx) -> int:  # pragma: no cover
        raise NotImplementedError


def _map_order_type(s: str) -> OrderType:
    return OrderType(s)
