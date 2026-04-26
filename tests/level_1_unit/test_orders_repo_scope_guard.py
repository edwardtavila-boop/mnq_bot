"""OrderBook ↔ repo-scope guard integration (v0.2.8 follow-up).

The pure-module guard (``mnq.venues.repo_scope``) and its tests
(``test_venues_repo_scope.py``) shipped in v0.2.8 / e9d7741. This
file pins the *live wiring*: every order submission flows through
``OrderBook.submit()``, and that path must enforce the guard so a
runtime caller that hands an MBT/MET symbol gets rejected at intake
(not silently routed to NinjaTraderVenue or a broker adapter).

Pin
---
* MBT/MET/BTC/USD/ETH/USD reach OrderBook.submit -> raise
  WrongRepoSymbolError, journal an ORDER_REJECTED event with
  ``wrong_repo_symbol=True``, do NOT call the gate chain
* MNQ reaches OrderBook.submit -> normal flow (journaled as
  ORDER_SUBMITTED, gate chain consulted, returns Order)
* Unknown symbol (e.g. ES, ZB) -> normal flow (operator opt-in path)
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from mnq.core.types import Side
from mnq.executor.orders import OrderBook, OrderType
from mnq.storage.journal import EventJournal
from mnq.storage.schema import ORDER_REJECTED
from mnq.venues.repo_scope import WrongRepoSymbolError


@pytest.fixture
def journal(tmp_path):
    db = tmp_path / "j.sqlite"
    return EventJournal(db)


@pytest.fixture
def book(journal):
    """OrderBook with no gate chain so we can isolate the repo-scope guard."""
    return OrderBook.unsafe_no_gate_chain(journal)


def test_submit_mbt_raises_wrong_repo_symbol(book) -> None:
    with pytest.raises(WrongRepoSymbolError, match="eta_engine"):
        book.submit(
            symbol="MBT",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )


def test_submit_met_raises_wrong_repo_symbol(book) -> None:
    with pytest.raises(WrongRepoSymbolError):
        book.submit(
            symbol="MET",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )


def test_submit_quarterly_roll_mbt_raises(book) -> None:
    """MBTU (Sep contract) must also be rejected, not just bare MBT."""
    with pytest.raises(WrongRepoSymbolError):
        book.submit(
            symbol="MBTU",
            side=Side.SHORT,
            qty=2,
            order_type=OrderType.MARKET,
        )


def test_submit_btcusd_raises(book) -> None:
    with pytest.raises(WrongRepoSymbolError):
        book.submit(
            symbol="BTC/USD",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )


def test_submit_with_leading_slash_raises(book) -> None:
    """``/MBT`` (TradingView-style) is normalized + rejected."""
    with pytest.raises(WrongRepoSymbolError):
        book.submit(
            symbol="/MBT",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )


def test_submit_mnq_succeeds(book) -> None:
    """The canonical in-scope symbol passes the guard."""
    order = book.submit(
        symbol="MNQ",
        side=Side.LONG,
        qty=1,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("21000.00"),
    )
    assert order is not None
    assert order.symbol == "MNQ"


def test_submit_unknown_symbol_succeeds(book) -> None:
    """Symbols outside both sets (operator opt-in path) pass the guard."""
    order = book.submit(
        symbol="ES",  # E-mini S&P -- not in either set
        side=Side.LONG,
        qty=1,
        order_type=OrderType.MARKET,
    )
    assert order is not None
    assert order.symbol == "ES"


def test_rejection_journals_wrong_repo_symbol_payload(
    book, journal,
) -> None:
    """The rejection event must carry ``wrong_repo_symbol=True`` and the
    full reason text so post-mortem replay can identify the boundary
    violation distinctly from a gate-chain block."""
    with pytest.raises(WrongRepoSymbolError):
        book.submit(
            symbol="MBT",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
    # Replay the journal and find the rejection
    rejections = list(journal.replay(event_types=(ORDER_REJECTED,)))
    assert len(rejections) == 1, f"expected 1 rejection, got {len(rejections)}"
    payload = rejections[0].payload
    assert payload.get("wrong_repo_symbol") is True
    assert payload.get("symbol") == "MBT"
    assert "eta_engine" in payload.get("reason", "")


def test_rejection_skips_gate_chain(journal) -> None:
    """If the symbol is wrong-repo, the gate chain is NOT evaluated.
    This matters because the gate chain may have side effects (heat
    budget queries, correlation lookups) that we don't want to run
    for a request that's structurally invalid."""
    gate_calls = {"n": 0}

    def _spy_evaluate():
        gate_calls["n"] += 1
        from mnq.risk import GateResult
        return True, [GateResult(True, "ok", "")]

    class _SpyChain:
        def evaluate(self):
            return _spy_evaluate()

    book = OrderBook(journal, _SpyChain())
    with pytest.raises(WrongRepoSymbolError):
        book.submit(
            symbol="MBT",
            side=Side.LONG,
            qty=1,
            order_type=OrderType.MARKET,
        )
    assert gate_calls["n"] == 0, (
        "gate chain was evaluated even though repo-scope guard "
        "should short-circuit before"
    )


def test_mnq_submission_does_call_gate_chain(journal) -> None:
    """Sanity: in-scope symbols still hit the gate chain (so we know
    the guard isn't accidentally short-circuiting valid traffic)."""
    gate_calls = {"n": 0}

    class _SpyChain:
        def evaluate(self):
            gate_calls["n"] += 1
            from mnq.risk import GateResult
            return True, [GateResult(True, "ok", "")]

    book = OrderBook(journal, _SpyChain())
    book.submit(
        symbol="MNQ",
        side=Side.LONG,
        qty=1,
        order_type=OrderType.MARKET,
    )
    assert gate_calls["n"] == 1
