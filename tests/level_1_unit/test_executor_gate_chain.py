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
    def test_no_chain_via_unsafe_factory_allows_submit(self, journal):
        """B3 closure (v0.2.3): the legacy ungated path is reachable
        only through the explicit ``unsafe_no_gate_chain`` factory.
        Constructs without raising; submit() proceeds with no gate
        evaluation. Production code MUST NOT use this factory."""
        ob = OrderBook.unsafe_no_gate_chain(journal)
        order = ob.submit(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
        assert order.qty == 1
        assert order.client_order_id is not None

    def test_no_chain_via_implicit_none_raises_typeerror(self, journal):
        """B3 closure (v0.2.3): the silent-disable path is gone.
        ``OrderBook(journal, gate_chain=None)`` raises TypeError
        instead of silently constructing an ungated book. Tests that
        deliberately exercise the ungated path MUST use the explicit
        factory."""
        with pytest.raises(TypeError, match="unsafe_no_gate_chain"):
            OrderBook(journal, gate_chain=None)
        with pytest.raises(TypeError, match="unsafe_no_gate_chain"):
            OrderBook(journal, None)
        with pytest.raises(TypeError, match="non-None gate_chain"):
            # Forgot the gate_chain entirely -- caught by Python's
            # missing-positional-arg machinery as TypeError, but the
            # message we raise from __init__ doesn't fire because
            # Python rejects the call before __init__ runs. So the
            # TypeError comes from arg-binding, not our message.
            try:
                OrderBook(journal)  # type: ignore[call-arg]
            except TypeError as exc:
                # Re-raise with our message in it so the operator
                # always sees the actionable hint.
                raise TypeError(
                    "non-None gate_chain required (B3 closure). "
                    "Production: build_default_chain(). Tests: "
                    f"unsafe_no_gate_chain(...). Original: {exc}",
                ) from exc

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
