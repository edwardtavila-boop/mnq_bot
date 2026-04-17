"""Level-1 tests for mnq.sim.layer2."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from mnq.core.types import Bar, Side
from mnq.sim.layer2 import (
    Layer2Engine,
    TradeLedger,
    run_layer2,
    simulate_exit_within_bar,
)
from mnq.sim.layer2.fills import simulate_entry_at_open
from mnq.spec.loader import load_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"


def _bar(
    i: int,
    o: float,
    h: float,
    lo: float,
    c: float,
    v: int = 100,
    start: datetime | None = None,
) -> Bar:
    start = start or datetime(2026, 1, 2, 14, 30, tzinfo=UTC)  # 09:30 NY
    return Bar(
        ts=start + timedelta(minutes=i),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=v,
        timeframe_sec=60,
    )


class TestFills:
    def test_long_entry_slipped_up(self) -> None:
        b = _bar(0, 100, 100.5, 99.5, 100.25)
        f = simulate_entry_at_open(b, Side.LONG, qty=1, slippage_ticks=1, tick_size=Decimal("0.25"))
        assert f.price == Decimal("100.25")  # open + 1 tick

    def test_short_entry_slipped_down(self) -> None:
        b = _bar(0, 100, 100.5, 99.5, 100.25)
        f = simulate_entry_at_open(b, Side.SHORT, qty=1, slippage_ticks=2, tick_size=Decimal("0.25"))
        assert f.price == Decimal("99.50")  # open - 2 ticks

    def test_long_stop_hits_first(self) -> None:
        # Long position; bar range touches both stop and TP → adverse (stop) wins.
        b = _bar(0, 100, 103, 95, 101)  # big range
        fill = simulate_exit_within_bar(
            b, position_side=Side.LONG, qty=1,
            stop_px=Decimal("98"), tp_px=Decimal("102"),
            stop_slippage_ticks=2, limit_slippage_ticks=0,
            tick_size=Decimal("0.25"),
        )
        assert fill is not None
        assert fill.reason == "stop"
        # Slipped 2 ticks below stop
        assert fill.price == Decimal("97.50")

    def test_long_target_fills_without_stop_reach(self) -> None:
        b = _bar(0, 100, 103, 99.5, 102)
        fill = simulate_exit_within_bar(
            b, position_side=Side.LONG, qty=1,
            stop_px=Decimal("98"), tp_px=Decimal("102"),
            stop_slippage_ticks=2, limit_slippage_ticks=0,
            tick_size=Decimal("0.25"),
        )
        assert fill is not None
        assert fill.reason == "take_profit"

    def test_no_exit_if_bar_inside(self) -> None:
        b = _bar(0, 100, 100.5, 99.5, 100.25)
        fill = simulate_exit_within_bar(
            b, position_side=Side.LONG, qty=1,
            stop_px=Decimal("95"), tp_px=Decimal("110"),
            tick_size=Decimal("0.25"),
        )
        assert fill is None

    def test_short_symmetry(self) -> None:
        b = _bar(0, 100, 103, 98, 99)
        fill = simulate_exit_within_bar(
            b, position_side=Side.SHORT, qty=1,
            stop_px=Decimal("102"), tp_px=Decimal("98"),
            stop_slippage_ticks=2, tick_size=Decimal("0.25"),
        )
        assert fill is not None
        assert fill.reason == "stop"
        assert fill.price == Decimal("102.50")  # 102 + 2 ticks


# -- A "toy strategy" for engine-level tests: always long on bar 5, never short --


class _ToyStrategy:
    """Minimal strategy that emits a pre-scripted signal plan."""

    def __init__(self, spec, signal_plan):
        self.spec = spec
        self._plan = list(signal_plan)  # list[(bar_index, Signal) | None]
        self._bar_index = -1
        self._position = 0

    def on_bar(self, bar):
        self._bar_index += 1
        for idx, sig in self._plan:
            if idx == self._bar_index:
                return sig
        return None

    def update_position(self, q):
        self._position = q


class TestEngine:
    @pytest.fixture(scope="class")
    def spec(self):
        return load_spec(BASELINE)

    def test_long_round_trip_scripted(self, spec) -> None:
        from mnq.core.types import OrderType, Signal

        # Bars: 0..19, we entry on bar 2, TP on bar 5.
        bars = [_bar(i, 100.0, 100.5, 99.5, 100.0) for i in range(20)]
        # Bar 6: price spike so TP at 101 hits
        bars[6] = _bar(6, 100.0, 102.0, 99.5, 101.5)

        signal = Signal(
            side=Side.LONG, qty=1, ref_price=Decimal("100.00"),
            stop=Decimal("99.00"), take_profit=Decimal("101.00"),
            order_type=OrderType.MARKET, limit_offset_ticks=0,
            market_fallback_ms=500, time_stop_bars=20,
            spec_hash="", spec_semver="0.1.0",
        )

        strat = _ToyStrategy(spec, [(2, signal)])
        engine = Layer2Engine(spec, strat, seed=0)  # type: ignore[arg-type]
        # Disable rejections for a deterministic assertion
        engine._rejection_p = 0.0
        ledger = engine.run(bars)
        assert ledger.n_trades == 1
        t = ledger.trades[0]
        assert t.side is Side.LONG
        assert t.exit_reason == "take_profit"
        assert t.pnl_points > 0

    def test_determinism(self, spec) -> None:
        from mnq.core.types import OrderType, Signal

        bars = [_bar(i, 100.0, 100.5, 99.5, 100.0) for i in range(30)]
        bars[6] = _bar(6, 100.0, 102.0, 99.5, 101.5)
        sig = Signal(
            side=Side.LONG, qty=1, ref_price=Decimal("100.00"),
            stop=Decimal("99.00"), take_profit=Decimal("101.00"),
            order_type=OrderType.MARKET, spec_hash="", spec_semver="0.1.0",
        )

        def _run():
            strat = _ToyStrategy(spec, [(2, sig)])
            e = Layer2Engine(spec, strat, seed=42)  # type: ignore[arg-type]
            e._rejection_p = 0.0
            return e.run(bars)

        a = _run()
        b = _run()
        assert len(a.trades) == len(b.trades) == 1
        assert a.trades[0].pnl_dollars == b.trades[0].pnl_dollars
        assert a.trades[0].entry_price == b.trades[0].entry_price
        assert a.trades[0].exit_price == b.trades[0].exit_price

    def test_generated_strategy_runs_full_synthetic_day(self, spec, tmp_path) -> None:
        """DoD check: render + run on a 1-day synthetic dataset; deterministic."""
        import importlib.util
        import sys

        from mnq.generators.python_exec import render_python

        src = render_python(spec)
        p = tmp_path / "s.py"
        p.write_text(src)
        sp = importlib.util.spec_from_file_location("gen_full_day", p)
        mod = importlib.util.module_from_spec(sp)  # type: ignore[arg-type]
        sys.modules["gen_full_day"] = mod
        sp.loader.exec_module(mod)  # type: ignore[union-attr]

        start = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)  # 09:30 NY
        # 390 bars = 6.5 hours of 1m RTH bars
        bars: list[Bar] = []
        for i in range(390):
            # Synthetic oscillation + mild trend
            base = 20000.0 + 0.2 * i
            osc = 2.0 * ((-1) ** (i // 5))
            p_ = base + osc
            bars.append(
                Bar(
                    ts=start + timedelta(minutes=i),
                    open=Decimal(str(p_)),
                    high=Decimal(str(p_ + 1.0)),
                    low=Decimal(str(p_ - 1.0)),
                    close=Decimal(str(p_ + 0.25)),
                    volume=200 + (i % 7) * 50,
                    timeframe_sec=60,
                )
            )

        def _one():
            inst = mod.build(spec)
            engine = Layer2Engine(spec, inst, seed=7)
            engine._rejection_p = 0.0
            return engine.run(bars)

        l1 = _one()
        l2 = _one()
        assert l1.n_trades == l2.n_trades
        # Determinism: same (possibly zero) set of trades reproduced identically.
        for a, b in zip(l1.trades, l2.trades, strict=True):
            assert a.entry_price == b.entry_price
            assert a.exit_price == b.exit_price
            assert a.exit_reason == b.exit_reason

    def test_run_layer2_helper(self, spec) -> None:

        bars = [_bar(i, 100.0, 100.5, 99.5, 100.0) for i in range(10)]
        strat = _ToyStrategy(spec, [])
        ledger = run_layer2(spec, strat, bars, seed=1)  # type: ignore[arg-type]
        assert isinstance(ledger, TradeLedger)
        assert ledger.n_trades == 0
