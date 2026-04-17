"""[REAL] Event-driven bar simulator.

    Layer2Engine.run(bars) -> TradeLedger

The engine consumes a sequence of primary-timeframe Bars, drives the
strategy's `on_bar`, and resolves entries + exits using the conservative
adverse-first intrabar model in `fills.py`.

Determinism: given the same spec, strategy, bars, and seed, two runs
produce identical TradeLedger objects (checked by a level-1 test).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from mnq.core.types import (
    Bar,
    Side,
    Signal,
    points_to_dollars,
)
from mnq.generators.python_exec.base import StrategyBase
from mnq.sim.layer2.fills import (
    SimulatedFill,
    simulate_entry_at_open,
    simulate_exit_within_bar,
)
from mnq.sim.layer2.latency import LatencyModel
from mnq.spec.schema import StrategySpec


@dataclass
class TradeRecord:
    entry_ts: datetime
    exit_ts: datetime
    side: Side
    qty: int
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal
    exit_reason: str
    r_multiple: Decimal
    pnl_points: Decimal
    pnl_dollars: Decimal
    commission_dollars: Decimal
    bars_held: int


@dataclass
class TradeLedger:
    trades: list[TradeRecord] = field(default_factory=list)

    def add(self, t: TradeRecord) -> None:
        self.trades.append(t)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def total_pnl_dollars(self) -> Decimal:
        return sum((t.pnl_dollars for t in self.trades), Decimal(0))

    @property
    def total_commission_dollars(self) -> Decimal:
        return sum((t.commission_dollars for t in self.trades), Decimal(0))

    # ---- breakdowns ------------------------------------------------------

    def breakdown_by_exit_reason(self) -> dict[str, dict[str, Decimal | int]]:
        """Group trades by `exit_reason` and return count + P&L per bucket.

        Useful for detecting pathologies like "80% of trades hit time_stop"
        (exit logic is dominating edge) or "all losers are stop hits but
        all winners are session_end" (strategy isn't actually taking
        profit via TP). Returned shape:

            {
              "take_profit": {"n": 42, "pnl": Decimal("1234.50"),
                              "pnl_avg": Decimal("29.39")},
              ...
            }
        """
        out: dict[str, dict[str, Decimal | int]] = {}
        for t in self.trades:
            bucket = out.setdefault(
                t.exit_reason, {"n": 0, "pnl": Decimal(0), "pnl_avg": Decimal(0)}
            )
            bucket["n"] = int(bucket["n"]) + 1
            bucket["pnl"] = Decimal(bucket["pnl"]) + t.pnl_dollars
        for bucket in out.values():
            n = int(bucket["n"])
            bucket["pnl_avg"] = Decimal(bucket["pnl"]) / Decimal(n) if n > 0 else Decimal(0)
        return out

    def breakdown_by_side(self) -> dict[str, dict[str, Decimal | int]]:
        """Long vs short P&L breakdown. Shape mirrors breakdown_by_exit_reason."""
        out: dict[str, dict[str, Decimal | int]] = {}
        for t in self.trades:
            key = t.side.value
            bucket = out.setdefault(key, {"n": 0, "pnl": Decimal(0), "pnl_avg": Decimal(0)})
            bucket["n"] = int(bucket["n"]) + 1
            bucket["pnl"] = Decimal(bucket["pnl"]) + t.pnl_dollars
        for bucket in out.values():
            n = int(bucket["n"])
            bucket["pnl_avg"] = Decimal(bucket["pnl"]) / Decimal(n) if n > 0 else Decimal(0)
        return out

    def win_rate(self) -> float:
        """Fraction of trades with strictly positive P&L (excludes scratches)."""
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl_dollars > 0)
        return wins / len(self.trades)

    def expectancy_dollars(self) -> Decimal:
        """Average dollar P&L per trade, net of commissions."""
        if not self.trades:
            return Decimal(0)
        return (self.total_pnl_dollars - self.total_commission_dollars) / Decimal(len(self.trades))


@dataclass
class _OpenPosition:
    signal: Signal
    entry_fill: SimulatedFill
    bars_held: int = 0


class Layer2Engine:
    """Bar-driven event loop. Single open position at a time."""

    def __init__(
        self,
        spec: StrategySpec,
        strategy: StrategyBase,
        *,
        seed: int = 0,
        latency: LatencyModel | None = None,
    ) -> None:
        self.spec = spec
        self.strategy = strategy
        self.rng = random.Random(seed)
        self.latency = latency or LatencyModel()
        self._tick = spec.instrument.tick_size
        self._commission_per_side = Decimal(str(spec.commission_model.per_contract_per_side_usd))
        self._entry_slippage_ticks = int(spec.slippage_model.entry_ticks)
        self._stop_slippage_ticks = int(spec.slippage_model.exit_stop_ticks)
        self._limit_slippage_ticks = int(spec.slippage_model.exit_limit_ticks)
        self._rejection_p = float(spec.slippage_model.rejection_probability)
        self._time_stop_bars = int(spec.exit.time_stop_bars or 0)

    def run(self, bars: Iterable[Bar]) -> TradeLedger:
        ledger = TradeLedger()
        bars = list(bars)

        open_pos: _OpenPosition | None = None
        pending_signal: Signal | None = None

        for bar in bars:
            # 1. Resolve pending entry onto *this* bar's open (delay = 1).
            if pending_signal is not None and open_pos is None:
                if self.rng.random() < self._rejection_p:
                    # rejected: drop silently
                    pending_signal = None
                else:
                    entry_fill = simulate_entry_at_open(
                        bar,
                        side=pending_signal.side,
                        qty=pending_signal.qty,
                        slippage_ticks=self._entry_slippage_ticks,
                        tick_size=self._tick,
                    )
                    open_pos = _OpenPosition(signal=pending_signal, entry_fill=entry_fill)
                    self.strategy.update_position(
                        entry_fill.qty * (1 if pending_signal.side is Side.LONG else -1)
                    )
                    pending_signal = None

            # 2. If holding a position, try to close it within this bar.
            if open_pos is not None:
                open_pos.bars_held += 1
                exit_fill = simulate_exit_within_bar(
                    bar,
                    position_side=open_pos.signal.side,
                    qty=open_pos.entry_fill.qty,
                    stop_px=open_pos.signal.stop,
                    tp_px=open_pos.signal.take_profit,
                    stop_slippage_ticks=self._stop_slippage_ticks,
                    limit_slippage_ticks=self._limit_slippage_ticks,
                    tick_size=self._tick,
                )
                # Time-stop if no hit and we've been in long enough.
                if (
                    exit_fill is None
                    and self._time_stop_bars > 0
                    and open_pos.bars_held >= self._time_stop_bars
                ):
                    exit_side = Side.SHORT if open_pos.signal.side is Side.LONG else Side.LONG
                    exit_fill = SimulatedFill(
                        ts=bar.ts,
                        side=exit_side,
                        price=bar.close,
                        qty=open_pos.entry_fill.qty,
                        reason="time_stop",
                    )
                if exit_fill is not None:
                    ledger.add(self._build_trade(open_pos, exit_fill))
                    open_pos = None
                    self.strategy.update_position(0)

            # 3. Feed the bar to the strategy; possibly emit a signal for T+1.
            if open_pos is None and pending_signal is None:
                sig = self.strategy.on_bar(bar)
                if sig is not None:
                    pending_signal = sig
            else:
                # Still feed the bar so features keep updating, but ignore any
                # signal it returns (the strategy itself gates on flat so it
                # shouldn't emit, but be defensive).
                self.strategy.on_bar(bar)

        # Close any residual position at the last bar's close.
        if open_pos is not None and bars:
            last = bars[-1]
            exit_side = Side.SHORT if open_pos.signal.side is Side.LONG else Side.LONG
            exit_fill = SimulatedFill(
                ts=last.ts,
                side=exit_side,
                price=last.close,
                qty=open_pos.entry_fill.qty,
                reason="session_end",
            )
            ledger.add(self._build_trade(open_pos, exit_fill))

        return ledger

    def _build_trade(self, pos: _OpenPosition, exit_fill: SimulatedFill) -> TradeRecord:
        side = pos.signal.side
        qty = pos.entry_fill.qty
        entry_px = pos.entry_fill.price
        exit_px = exit_fill.price

        diff = (exit_px - entry_px) if side is Side.LONG else (entry_px - exit_px)
        pnl_points = diff
        pnl_dollars = points_to_dollars(pnl_points, qty, self.spec.instrument.point_value)
        commission = self._commission_per_side * 2 * qty

        stop_distance = abs(entry_px - pos.signal.stop)
        r = Decimal(0) if stop_distance == 0 else (pnl_points / stop_distance)

        return TradeRecord(
            entry_ts=pos.entry_fill.ts,
            exit_ts=exit_fill.ts,
            side=side,
            qty=qty,
            entry_price=entry_px,
            exit_price=exit_px,
            stop_price=pos.signal.stop,
            take_profit_price=pos.signal.take_profit,
            exit_reason=exit_fill.reason,
            r_multiple=r,
            pnl_points=pnl_points,
            pnl_dollars=pnl_dollars,
            commission_dollars=commission,
            bars_held=pos.bars_held,
        )


def run_layer2(
    spec: StrategySpec,
    strategy: StrategyBase,
    bars: Iterable[Bar],
    seed: int = 0,
) -> TradeLedger:
    """Convenience: build an engine and run once."""
    engine = Layer2Engine(spec, strategy, seed=seed)
    return engine.run(bars)
