"""Unit tests for the ShadowVenue scaffold (``mnq.venues.shadow``).

Batch 4A. The shadow venue is the future-broker surface — it MUST be:

- Deterministic: same (signal, price, ts) sequence → same fill stream
- Append-only journal: one JSONL line per fill, parseable, round-trippable
- Never-rejects in MVP (upstream risk already cleared)
- Side/qty/price faithful: records the signal's side/qty, price caller
  provides, venue tag = "shadow"
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from mnq.core.types import OrderType, Side, Signal
from mnq.venues.shadow import ShadowOrderResult, ShadowVenue


def _mk_signal(
    side: Side = Side.LONG,
    qty: int = 1,
    ref: Decimal = Decimal("24000"),
    stop_distance: Decimal = Decimal("10"),
    tp_distance: Decimal = Decimal("20"),
) -> Signal:
    if side is Side.LONG:
        stop = ref - stop_distance
        tp = ref + tp_distance
    else:
        stop = ref + stop_distance
        tp = ref - tp_distance
    return Signal(
        side=side,
        qty=qty,
        ref_price=ref,
        stop=stop,
        take_profit=tp,
        order_type=OrderType.MARKET,
        spec_hash="abcd1234",
    )


def _ts(n: int = 0) -> datetime:
    return datetime(2026, 4, 16, 14, 30, tzinfo=UTC).replace(minute=30 + n)


# --- Contract shape ----------------------------------------------------------


class TestPlaceOrderContract:
    def test_returns_shadow_order_result(self) -> None:
        venue = ShadowVenue()
        sig = _mk_signal()
        result = venue.place_order(sig, at_price=Decimal("24000"), at_ts=_ts())
        assert isinstance(result, ShadowOrderResult)
        assert result.rejected is False
        assert result.fill.venue == "shadow"
        assert result.fill.side is Side.LONG
        assert result.fill.qty == 1
        assert result.fill.price == Decimal("24000")

    def test_order_id_is_deterministic_and_sequential(self) -> None:
        venue = ShadowVenue()
        r1 = venue.place_order(_mk_signal(), at_price=Decimal("24000"), at_ts=_ts(0))
        r2 = venue.place_order(_mk_signal(), at_price=Decimal("24001"), at_ts=_ts(1))
        r3 = venue.place_order(_mk_signal(), at_price=Decimal("24002"), at_ts=_ts(2))
        assert r1.fill.order_id == "shadow-000001"
        assert r2.fill.order_id == "shadow-000002"
        assert r3.fill.order_id == "shadow-000003"
        assert r1.fill.venue_fill_id == "shadow-000001-F"

    def test_spec_hash_propagates_to_fill(self) -> None:
        venue = ShadowVenue()
        sig = _mk_signal()
        result = venue.place_order(sig, at_price=Decimal("24000"), at_ts=_ts())
        assert result.fill.spec_hash == "abcd1234"

    def test_commission_is_recorded(self) -> None:
        venue = ShadowVenue(commission_per_side=Decimal("1.25"))
        result = venue.place_order(_mk_signal(), at_price=Decimal("24000"), at_ts=_ts())
        assert result.fill.commission == Decimal("1.25")


# --- Fill history ------------------------------------------------------------


class TestGetFills:
    def test_empty_initially(self) -> None:
        assert ShadowVenue().get_fills() == []

    def test_tracks_order_history(self) -> None:
        venue = ShadowVenue()
        venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(0))
        venue.place_order(_mk_signal(Side.SHORT), at_price=Decimal("24010"), at_ts=_ts(1))
        fills = venue.get_fills()
        assert len(fills) == 2
        assert fills[0].side is Side.LONG
        assert fills[1].side is Side.SHORT

    def test_get_fills_returns_copy(self) -> None:
        venue = ShadowVenue()
        venue.place_order(_mk_signal(), at_price=Decimal("24000"), at_ts=_ts())
        snapshot = venue.get_fills()
        snapshot.clear()
        assert len(venue.get_fills()) == 1  # original untouched


# --- JSONL journal -----------------------------------------------------------


class TestJsonlJournal:
    def test_append_mode_writes_one_line_per_fill(self, tmp_path) -> None:
        j = tmp_path / "shadow_fills.jsonl"
        with ShadowVenue(journal_path=j) as venue:
            venue.place_order(_mk_signal(Side.LONG), at_price=Decimal("24000"), at_ts=_ts(0))
            venue.place_order(_mk_signal(Side.SHORT), at_price=Decimal("24010"), at_ts=_ts(1))
        lines = j.read_text().strip().split("\n")
        assert len(lines) == 2
        recs = [json.loads(line) for line in lines]
        assert recs[0]["side"] == "long"
        assert recs[0]["qty"] == 1
        assert recs[0]["venue"] == "shadow"
        assert recs[0]["price"] == "24000"
        assert recs[1]["side"] == "short"

    def test_journal_survives_second_venue_instance(self, tmp_path) -> None:
        j = tmp_path / "shadow_fills.jsonl"
        with ShadowVenue(journal_path=j) as v1:
            v1.place_order(_mk_signal(), at_price=Decimal("24000"), at_ts=_ts(0))
        with ShadowVenue(journal_path=j) as v2:
            v2.place_order(_mk_signal(), at_price=Decimal("24001"), at_ts=_ts(1))
        lines = j.read_text().strip().split("\n")
        assert len(lines) == 2  # append mode preserves prior run

    def test_no_journal_path_skips_disk_writes(self) -> None:
        venue = ShadowVenue(journal_path=None)  # in-memory only
        venue.place_order(_mk_signal(), at_price=Decimal("24000"), at_ts=_ts())
        venue.close()  # should be a no-op, not raise


# --- Determinism -------------------------------------------------------------


class TestDeterminism:
    def test_same_signal_sequence_produces_same_fills(self) -> None:
        seq = [
            (_mk_signal(Side.LONG), Decimal("24000"), _ts(0)),
            (_mk_signal(Side.SHORT), Decimal("24010"), _ts(1)),
            (_mk_signal(Side.LONG, qty=2), Decimal("24020"), _ts(2)),
        ]
        v1 = ShadowVenue()
        v2 = ShadowVenue()
        for sig, px, ts in seq:
            v1.place_order(sig, at_price=px, at_ts=ts)
            v2.place_order(sig, at_price=px, at_ts=ts)
        a = v1.get_fills()
        b = v2.get_fills()
        assert len(a) == len(b)
        for fa, fb in zip(a, b, strict=True):
            assert fa.order_id == fb.order_id
            assert fa.side == fb.side
            assert fa.qty == fb.qty
            assert fa.price == fb.price
            assert fa.ts == fb.ts


# --- Multi-qty / short -------------------------------------------------------


class TestMultiQtyAndShort:
    def test_short_side_recorded(self) -> None:
        venue = ShadowVenue()
        result = venue.place_order(
            _mk_signal(Side.SHORT), at_price=Decimal("24000"), at_ts=_ts()
        )
        assert result.fill.side is Side.SHORT

    def test_qty_three_preserved(self) -> None:
        venue = ShadowVenue()
        result = venue.place_order(
            _mk_signal(Side.LONG, qty=3), at_price=Decimal("24000"), at_ts=_ts()
        )
        assert result.fill.qty == 3

    @pytest.mark.parametrize("qty", [1, 2, 5, 10])
    def test_qty_roundtrip_via_jsonl(self, tmp_path, qty: int) -> None:
        j = tmp_path / "fills.jsonl"
        with ShadowVenue(journal_path=j) as venue:
            venue.place_order(
                _mk_signal(Side.LONG, qty=qty), at_price=Decimal("24000"), at_ts=_ts()
            )
        rec = json.loads(j.read_text().strip())
        assert rec["qty"] == qty
