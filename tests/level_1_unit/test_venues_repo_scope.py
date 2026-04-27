"""Tests for ``mnq.venues.repo_scope`` -- structural guard pinning the
locked two-project decision (CLAUDE.md 2026-04-17).

Pin the contract:

  > Symbols that belong to eta_engine (MBT, MET, BTC/USD, ETH/USD,
  > etc.) MUST NOT be routable through mnq_bot's venue layer. The
  > guard fires regardless of leading slash, case, or quarterly-roll
  > suffix.

If a future PR adds MBT/MET handling here, either:
  (a) the guard correctly fires and this test stays green by virtue of
      the guard catching the drift; OR
  (b) the operator deliberately removes the guard, in which case this
      test fails loudly and forces a CLAUDE.md update + PR review.
"""

from __future__ import annotations

import pytest

from mnq.venues.repo_scope import (
    ETA_ENGINE_SYMBOLS,
    MNQ_BOT_SYMBOLS,
    WrongRepoSymbolError,
    assert_symbol_in_repo_scope,
    is_in_repo_scope,
)

# ---------------------------------------------------------------------------
# Symbol-set invariants
# ---------------------------------------------------------------------------


def test_eta_engine_symbols_includes_mbt_met() -> None:
    """The two canonical layer-3 symbols MUST be in the redirect set."""
    assert "MBT" in ETA_ENGINE_SYMBOLS
    assert "MET" in ETA_ENGINE_SYMBOLS


def test_eta_engine_symbols_includes_quarterly_roll_codes() -> None:
    """The four CME quarterly month codes (H Mar, M Jun, U Sep, Z Dec)
    for MBT and MET must be covered explicitly. If a future-month roll
    adds a new code, this test fails and forces the guard to update."""
    months = ("H", "M", "U", "Z")
    for code in months:
        assert f"MBT{code}" in ETA_ENGINE_SYMBOLS, f"missing MBT{code} (month {code})"
        assert f"MET{code}" in ETA_ENGINE_SYMBOLS, f"missing MET{code} (month {code})"


def test_eta_engine_symbols_includes_spot_crypto() -> None:
    """Spot crypto (BTC/USD, ETH/USD) is eta_engine territory."""
    assert "BTC/USD" in ETA_ENGINE_SYMBOLS
    assert "ETH/USD" in ETA_ENGINE_SYMBOLS
    assert "BTCUSD" in ETA_ENGINE_SYMBOLS  # no-slash variant
    assert "ETHUSD" in ETA_ENGINE_SYMBOLS


def test_mnq_bot_symbols_includes_mnq_and_nq() -> None:
    """The MNQ contract family is THIS repo's scope."""
    assert "MNQ" in MNQ_BOT_SYMBOLS
    assert "NQ" in MNQ_BOT_SYMBOLS


def test_eta_engine_and_mnq_sets_are_disjoint() -> None:
    """Sanity: no symbol appears in both sets. Drift here means the
    boundary is unclear."""
    overlap = ETA_ENGINE_SYMBOLS & MNQ_BOT_SYMBOLS
    assert not overlap, f"sets overlap: {overlap}"


# ---------------------------------------------------------------------------
# assert_symbol_in_repo_scope
# ---------------------------------------------------------------------------


def test_assert_rejects_mbt() -> None:
    with pytest.raises(WrongRepoSymbolError, match="eta_engine"):
        assert_symbol_in_repo_scope("MBT")


def test_assert_rejects_met() -> None:
    with pytest.raises(WrongRepoSymbolError, match="eta_engine"):
        assert_symbol_in_repo_scope("MET")


def test_assert_rejects_quarterly_roll_codes() -> None:
    """A quarterly-rolled MBT contract (e.g. MBTU = Sep) must still be
    rejected."""
    with pytest.raises(WrongRepoSymbolError):
        assert_symbol_in_repo_scope("MBTU")
    with pytest.raises(WrongRepoSymbolError):
        assert_symbol_in_repo_scope("METZ")


def test_assert_rejects_with_leading_slash() -> None:
    """``/MBT`` (TradingView-style) must be normalized + rejected."""
    with pytest.raises(WrongRepoSymbolError):
        assert_symbol_in_repo_scope("/MBT")


def test_assert_rejects_lowercase() -> None:
    """Case-insensitive normalization."""
    with pytest.raises(WrongRepoSymbolError):
        assert_symbol_in_repo_scope("mbt")


def test_assert_rejects_spot_btc_eth() -> None:
    with pytest.raises(WrongRepoSymbolError):
        assert_symbol_in_repo_scope("BTC/USD")
    with pytest.raises(WrongRepoSymbolError):
        assert_symbol_in_repo_scope("ETHUSD")


def test_assert_allows_mnq() -> None:
    """No exception for the canonical in-scope symbol."""
    assert_symbol_in_repo_scope("MNQ")
    assert_symbol_in_repo_scope("/MNQ")
    assert_symbol_in_repo_scope("MNQH")  # quarterly roll


def test_assert_allows_nq() -> None:
    assert_symbol_in_repo_scope("NQ")


def test_assert_allows_unknown_symbol() -> None:
    """Symbols outside both sets are allowed (operator opt-in path)."""
    assert_symbol_in_repo_scope("ES")  # E-mini S&P
    assert_symbol_in_repo_scope("ZB")  # 30y Treasury
    assert_symbol_in_repo_scope("CL")  # Crude oil


def test_error_message_mentions_eta_engine_and_redirect() -> None:
    """The exception text must point the operator at eta_engine
    AND at the file to edit if the boundary genuinely needs to change."""
    with pytest.raises(WrongRepoSymbolError) as exc_info:
        assert_symbol_in_repo_scope("MBT")
    msg = str(exc_info.value)
    assert "eta_engine" in msg
    assert "cme_micro_crypto" in msg
    assert "repo_scope.py" in msg
    assert "CLAUDE.md" in msg


# ---------------------------------------------------------------------------
# is_in_repo_scope predicate
# ---------------------------------------------------------------------------


def test_is_in_repo_scope_true_for_mnq() -> None:
    assert is_in_repo_scope("MNQ") is True


def test_is_in_repo_scope_false_for_mbt() -> None:
    assert is_in_repo_scope("MBT") is False
    assert is_in_repo_scope("MET") is False
    assert is_in_repo_scope("/MBT") is False
    assert is_in_repo_scope("BTC/USD") is False


def test_is_in_repo_scope_true_for_unknown() -> None:
    """Symbols outside both sets are allowed -- the predicate returns
    True (doesn't reject)."""
    assert is_in_repo_scope("CL") is True
    assert is_in_repo_scope("ES") is True
