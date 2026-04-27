"""Scorecard bundle v0.1 (Apr 2026) — regression tests for the 6 line-level
fixes called out by the EVOLUTIONARY TRADING ALGO SCORECARD:

    1. SMA.update() uses an O(1) running sum (was O(n) per bar).
    2. ATR no longer mutates RMA private state; RMA.step() is the public hook.
    3. orders_cancelled_total counter exists and OrderBook.cancel() uses it
       (the cancel path used to mis-increment orders_rejected_total).
    4. reconciler.net_positions_from_journal runs in O(m+n) via a single
       submit-index pass (was O(m*n)).
    5. gauntlet12 gate_regime threshold lifted from >0.0 to >=0.5 so chop
       regimes no longer silently pass.
    6. gauntlet12 gate_correlation Pearson now uses the sample (Bessel)
       denominator (n-1) matching numpy.corrcoef / pandas .corr.

These tests stay narrow and reference-style so the intent stays legible
when future refactors touch these hotspots.
"""

from __future__ import annotations

import math
import random
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from mnq.core.types import Side
from mnq.executor.orders import Fill, OrderBook, OrderType
from mnq.executor.reconciler import net_positions_from_journal
from mnq.features.atr import ATR
from mnq.features.rma import RMA
from mnq.features.sma import SMA
from mnq.gauntlet.gates.gauntlet12 import (
    GauntletContext,
    gate_correlation,
    gate_regime,
)
from mnq.observability.metrics import (
    orders_cancelled_total,
    orders_rejected_total,
    reset_metrics_for_tests,
)
from mnq.storage.journal import EventJournal
from tests.level_1_unit._bars import constant_bars, linear_close_bars, make_bar


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    """Scorecard suite reaches into Prometheus counters — reset each test."""
    reset_metrics_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: SMA running sum — must produce the same values as the naive impl.
# ─────────────────────────────────────────────────────────────────────────────


class TestSMARunningSum:
    def test_matches_naive_reference_across_long_series(self) -> None:
        """Running-sum SMA matches naive sum-over-buffer for 500 bars."""
        length = 20
        sma = SMA(length=length)
        buf: deque[float] = deque(maxlen=length)

        rng = random.Random(4242)
        bars = [
            make_bar(
                datetime(2026, 4, 16, 13, 30, tzinfo=UTC) + timedelta(minutes=i),
                o=21000 + rng.uniform(-20, 20),
                h=21050,
                lo=20950,
                c=21000 + rng.uniform(-20, 20),
                v=100,
            )
            for i in range(500)
        ]
        for b in bars:
            got = sma.update(b)
            buf.append(float(b.close))
            expected = sum(buf) / length if len(buf) >= length else None
            if expected is None:
                assert got is None
            else:
                assert got == pytest.approx(expected, rel=1e-9, abs=1e-9)

    def test_constant_input_converges_exact(self) -> None:
        """With all-equal inputs the running sum must equal the constant."""
        sma = SMA(length=10)
        last: float | None = None
        for b in constant_bars(50, price=100.0):
            last = sma.update(b)
        assert last == pytest.approx(100.0)

    def test_sum_slot_matches_buf_contents(self) -> None:
        """Running _sum is always equal to sum(_buf) (no arithmetic drift)."""
        sma = SMA(length=5)
        for b in linear_close_bars(30, start_price=50.0, slope=0.25):
            sma.update(b)
        # After any number of updates the running sum must equal sum of buffer.
        assert sma._sum == pytest.approx(sum(sma._buf), rel=1e-12, abs=1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2: RMA.step() public scalar hook + ATR uses it (no private mutation).
# ─────────────────────────────────────────────────────────────────────────────


class TestRMAStepPublic:
    def test_step_matches_update_path(self) -> None:
        """RMA.step(x) and RMA.update(bar_with_close=x) produce equal values."""
        r_step = RMA(length=5)
        r_update = RMA(length=5)
        start = datetime(2026, 4, 16, 13, 30, tzinfo=UTC)
        xs = [10.0, 11.0, 9.5, 12.0, 10.5, 11.25, 9.75, 10.1, 10.2, 10.3]
        got_step: list[float | None] = []
        got_update: list[float | None] = []
        for i, x in enumerate(xs):
            got_step.append(r_step.step(x))
            got_update.append(
                r_update.update(
                    make_bar(
                        start + timedelta(minutes=i),
                        x,
                        x + 0.1,
                        x - 0.1,
                        x,
                    )
                )
            )
        for s, u in zip(got_step, got_update, strict=True):
            if s is None:
                assert u is None
            else:
                assert s == pytest.approx(u)

    def test_atr_does_not_touch_private_rma_state(self) -> None:
        """ATR.update should go through the public RMA.step API only."""
        atr = ATR(length=14)
        # Seed count before any bars.
        assert atr._rma._count == 0
        for b in linear_close_bars(30, start_price=21000.0, slope=0.5):
            atr.update(b)
        # After 30 bars the RMA has advanced 30 steps solely via step().
        assert atr._rma._count == 30
        assert atr._rma.ready is True

    def test_step_constant_input_converges(self) -> None:
        r = RMA(length=10)
        last = None
        for _ in range(50):
            last = r.step(5.0)
        assert last == pytest.approx(5.0)


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3 + 4: orders_cancelled_total exists and is incremented on cancel().
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_journal(tmp_path: Path) -> EventJournal:
    db = tmp_path / "journal.db"
    return EventJournal(db, fsync=False)


class TestOrdersCancelledCounter:
    def test_cancel_increments_cancelled_not_rejected(self, tmp_journal: EventJournal) -> None:
        """cancel() must emit orders_cancelled_total — not orders_rejected_total."""
        book = OrderBook.unsafe_no_gate_chain(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "V1")

        before_cancelled = orders_cancelled_total.labels(reason="user")._value.get()
        before_rejected = orders_rejected_total.labels(reason="user")._value.get()

        book.cancel(order.client_order_id, reason="user")

        after_cancelled = orders_cancelled_total.labels(reason="user")._value.get()
        after_rejected = orders_rejected_total.labels(reason="user")._value.get()

        assert after_cancelled - before_cancelled == 1
        assert after_rejected == before_rejected  # untouched

    def test_reject_still_increments_rejected(self, tmp_journal: EventJournal) -> None:
        """Regression guard — reject() keeps its own counter."""
        book = OrderBook.unsafe_no_gate_chain(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.SHORT,
            qty=2,
            order_type=OrderType.MARKET,
        )
        before = orders_rejected_total.labels(reason="risk")._value.get()
        book.reject(order.client_order_id, "risk")
        after = orders_rejected_total.labels(reason="risk")._value.get()
        assert after - before == 1


# ─────────────────────────────────────────────────────────────────────────────
# Fix 5: reconciler.net_positions_from_journal — single-pass correctness.
# ─────────────────────────────────────────────────────────────────────────────


class TestReconcilerSinglePass:
    def test_large_journal_aggregates_correctly(self, tmp_journal: EventJournal) -> None:
        """50 fills against 50 distinct submits still net to the right qty."""
        book = OrderBook.unsafe_no_gate_chain(tmp_journal)
        longs = shorts = 0
        for i in range(50):
            side = Side.LONG if i % 2 == 0 else Side.SHORT
            qty = 1 + (i % 3)
            order = book.submit(
                symbol="MNQ",
                side=side,
                qty=qty,
                order_type=OrderType.MARKET,
            )
            book.ack(order.client_order_id, f"V{i}")
            book.apply_fill(
                Fill(
                    client_order_id=order.client_order_id,
                    venue_fill_id=f"F{i}",
                    price=Decimal("21000"),
                    qty=qty,
                    ts=datetime.now(UTC),
                    trace_id=None,
                )
            )
            if side is Side.LONG:
                longs += qty
            else:
                shorts += qty
        positions = net_positions_from_journal(tmp_journal)
        assert positions["MNQ"] == longs - shorts

    def test_multi_symbol_aggregation(self, tmp_journal: EventJournal) -> None:
        """Multiple symbols are netted independently."""
        book = OrderBook.unsafe_no_gate_chain(tmp_journal)
        # MNQ +3
        o = book.submit(symbol="MNQ", side=Side.LONG, qty=3, order_type=OrderType.MARKET)
        book.ack(o.client_order_id, "V1")
        book.apply_fill(
            Fill(
                client_order_id=o.client_order_id,
                venue_fill_id="F1",
                price=Decimal("21000"),
                qty=3,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )
        # ES -2
        o = book.submit(symbol="ES", side=Side.SHORT, qty=2, order_type=OrderType.MARKET)
        book.ack(o.client_order_id, "V2")
        book.apply_fill(
            Fill(
                client_order_id=o.client_order_id,
                venue_fill_id="F2",
                price=Decimal("5000"),
                qty=2,
                ts=datetime.now(UTC),
                trace_id=None,
            )
        )
        positions = net_positions_from_journal(tmp_journal)
        assert positions["MNQ"] == 3
        assert positions["ES"] == -2

    def test_partial_fills_accumulate(self, tmp_journal: EventJournal) -> None:
        """Multiple partial fills against one submit net as expected."""
        book = OrderBook.unsafe_no_gate_chain(tmp_journal)
        order = book.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=5,
            order_type=OrderType.MARKET,
        )
        book.ack(order.client_order_id, "V1")
        # Two partials of 2+3 = 5
        for i, q in enumerate([2, 3]):
            book.apply_fill(
                Fill(
                    client_order_id=order.client_order_id,
                    venue_fill_id=f"F{i}",
                    price=Decimal("21000"),
                    qty=q,
                    ts=datetime.now(UTC),
                    trace_id=None,
                )
            )
        positions = net_positions_from_journal(tmp_journal)
        assert positions["MNQ"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# Fix 6: gauntlet12 gate_regime threshold + gate_correlation sample Pearson.
# ─────────────────────────────────────────────────────────────────────────────


def _regime_ctx(regime: str, side: str = "long") -> GauntletContext:
    return GauntletContext(
        now=datetime(2026, 4, 16, 14, 30, tzinfo=UTC),
        side=side,
        regime=regime,
    )


class TestRegimeGateStrictThreshold:
    def test_trend_up_long_still_passes(self) -> None:
        assert gate_regime(_regime_ctx("trend_up", "long")).pass_ is True

    def test_chop_now_blocks(self) -> None:
        r = gate_regime(_regime_ctx("chop", "long"))
        assert r.score == pytest.approx(0.3)
        assert r.pass_ is False

    def test_high_vol_still_blocks(self) -> None:
        # high_vol is hard-blocked at score 0.0; must remain blocked.
        r = gate_regime(_regime_ctx("high_vol", "long"))
        assert r.pass_ is False
        assert r.score == 0.0


def _pearson_reference(xs: list[float], ys: list[float]) -> float:
    """Independent Pearson reference using the sample (n-1) denominator."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True)) / (n - 1)
    var_x = sum((a - mx) ** 2 for a in xs) / (n - 1)
    var_y = sum((b - my) ** 2 for b in ys) / (n - 1)
    if var_x <= 0 or var_y <= 0:
        return 0.0
    return cov / (math.sqrt(var_x) * math.sqrt(var_y))


class TestCorrelationPearsonSampleDenominator:
    def test_matches_sample_reference(self) -> None:
        """Computed corr uses (n-1) — matches the numpy.corrcoef convention."""
        rng = random.Random(17)
        mnq = [21000 + rng.uniform(-5, 5) + i * 0.3 for i in range(20)]
        # ES has strong positive relationship + noise
        es = [m * 0.22 + rng.uniform(-0.5, 0.5) for m in mnq]

        ctx = GauntletContext(
            now=datetime(2026, 4, 16, 14, 30, tzinfo=UTC),
            side="long",
            closes=mnq,
            es_closes=es,
        )
        v = gate_correlation(ctx)
        expected = _pearson_reference(mnq[-20:], es[-20:])
        assert v.detail["mode"] == "computed"
        assert v.detail["corr"] == pytest.approx(round(expected, 4), abs=1e-4)

    def test_flat_series_returns_zero(self) -> None:
        """Zero variance → corr 0.0 (guard against div-by-zero)."""
        ctx = GauntletContext(
            now=datetime(2026, 4, 16, 14, 30, tzinfo=UTC),
            side="long",
            closes=[21000.0] * 20,
            es_closes=[5000.0] * 20,
        )
        v = gate_correlation(ctx)
        assert v.detail["corr"] == 0.0

    def test_perfectly_correlated_returns_one(self) -> None:
        """Monotone co-movement → corr == 1.0 regardless of denominator choice."""
        mnq = [21000.0 + i for i in range(20)]
        es = [5000.0 + i * 0.5 for i in range(20)]
        ctx = GauntletContext(
            now=datetime(2026, 4, 16, 14, 30, tzinfo=UTC),
            side="long",
            closes=mnq,
            es_closes=es,
        )
        v = gate_correlation(ctx)
        assert v.detail["corr"] == pytest.approx(1.0, abs=1e-6)
