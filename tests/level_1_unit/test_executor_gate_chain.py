"""Executor ↔ gate-chain integration — verifies `OrderBook` blocks on DENY."""
from __future__ import annotations

import pytest

from mnq.core.types import Side
from mnq.executor.orders import OrderBlocked, OrderBook, OrderType
from mnq.risk import GateChain, GateResult
from mnq.storage.journal import EventJournal


def _deny(name: str, reason: str):
    def _g() -> GateResult:
        return GateResult(False, name, reason)
    _g.name = name  # type: ignore[attr-defined]
    return _g


def _allow(name: str = "ok"):
    def _g() -> GateResult:
        return GateResult(True, name, "ok")
    _g.name = name  # type: ignore[attr-defined]
    return _g


@pytest.fixture
def journal(tmp_path):
    db = tmp_path / "j.sqlite"
    return EventJournal(db)


class TestGateChainIntegration:
    def test_no_chain_allows_normally(self, journal):
        ob = OrderBook(journal)
        order = ob.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        assert order.qty == 1
        assert order.client_order_id is not None

    def test_allowing_chain_permits_submit(self, journal):
        chain = GateChain(gates=(_allow("a"), _allow("b")))
        ob = OrderBook(journal, gate_chain=chain)
        order = ob.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        assert order is not None

    def test_denying_chain_raises_order_blocked(self, journal):
        chain = GateChain(gates=(_allow("heartbeat"), _deny("governor", "cap breached")))
        ob = OrderBook(journal, gate_chain=chain)
        with pytest.raises(OrderBlocked) as excinfo:
            ob.submit(
                symbol="MNQ",
                side=Side.LONG,
                qty=1,
                order_type=OrderType.MARKET,
            )
        assert excinfo.value.gate == "governor"
        assert "cap breached" in excinfo.value.reason

    def test_block_is_journaled(self, journal, tmp_path):
        chain = GateChain(gates=(_deny("heartbeat", "stale"),))
        ob = OrderBook(journal, gate_chain=chain)
        with pytest.raises(OrderBlocked):
            ob.submit(
                symbol="MNQ",
                side=Side.LONG,
                qty=1,
                order_type=OrderType.MARKET,
            )
        # Verify the journal got an ORDER_REJECTED with gate_blocked=True
        import sqlite3
        conn = sqlite3.connect(str(journal.path))
        rows = conn.execute(
            "SELECT event_type, payload FROM events WHERE event_type = 'order.rejected'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        import json
        payload = json.loads(rows[0][1])
        assert payload["gate_blocked"] is True
        assert payload["gate"] == "heartbeat"
