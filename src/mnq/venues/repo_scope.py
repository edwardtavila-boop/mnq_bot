"""Repo-scope enforcement: keep mnq_bot focused on MNQ.

Per the locked two-project decision (CLAUDE.md, 2026-04-17),
``eta_engine`` and ``mnq_bot`` are SEPARATE codebases. ``mnq_bot``
trades MNQ on IBKR. ``eta_engine`` runs the multi-bot perp portfolio
and the layer-3 casino tier (CME micro crypto futures: MBT, MET).

Operator update 2026-04-26: VPS is rotating layer-3 from offshore
perps to **CME micro crypto futures (MBT/MET) on IBKR**. That work
lives in ``eta_engine/venues/cme_micro_crypto.py``, NOT here. To
prevent silent drift -- where someone wires MBT/MET handling into
mnq_bot and starts blurring the codebase boundary -- this module pins
which symbols mnq_bot is allowed to route.

If a future code path tries to submit an order for an out-of-scope
symbol, :func:`assert_symbol_in_repo_scope` raises with a clear
redirect to ``eta_engine``. This is structurally identical to the
``DORMANT_BROKERS`` enforcement in :mod:`mnq.venues.dormancy`.

Why a guard not a rename
------------------------
Renaming ``mnq_bot`` to ``mnq_micro_bot`` would semantically scope
it but it'd be a churn-heavy refactor that adds zero capability.
The guard pattern is one line of defensive code + one regression
test. It catches drift without forcing a rename.
"""
from __future__ import annotations

# Symbols that belong to eta_engine, NOT mnq_bot. Routing any order
# for these symbols through mnq_bot's venue layer is a category error
# (the strategy code, journal, and risk caps in this repo are calibrated
# for MNQ, not MBT/MET).
ETA_ENGINE_SYMBOLS: frozenset[str] = frozenset({
    # CME micro crypto futures (layer-3 casino tier).
    # Quarterly month codes: H (Mar), M (Jun), U (Sep), Z (Dec).
    "MBT", "MBTH", "MBTM", "MBTU", "MBTZ",  # Micro Bitcoin futures
    "MET", "METH", "METM", "METU", "METZ",  # Micro Ether futures
    # Spot crypto cross-references (eta_engine routes these).
    "BTC/USD", "ETH/USD",
    "BTCUSD", "ETHUSD",
})

# Symbols this repo is calibrated for. Order routing for these proceeds
# normally. Anything not in this set AND in ETA_ENGINE_SYMBOLS triggers
# the redirect. Anything not in EITHER set is allowed (treated as a
# user-defined custom symbol the operator opted into).
MNQ_BOT_SYMBOLS: frozenset[str] = frozenset({
    # MNQ-specific contract codes per quarterly roll.
    # Quarterly month codes: H (Mar), M (Jun), U (Sep), Z (Dec).
    "MNQ",
    "MNQH", "MNQM", "MNQU", "MNQZ",
    # NQ (Mini Nasdaq), the underlying parent contract.
    "NQ",
    "NQH", "NQM", "NQU", "NQZ",
})


class WrongRepoSymbolError(RuntimeError):
    """Raised when an order-routing path picks an out-of-scope symbol.

    The exception text names the symbol, identifies which repo SHOULD
    handle it, and references the locked two-project decision so a
    reviewer can resolve the drift.
    """


def assert_symbol_in_repo_scope(symbol: str) -> None:
    """Raise :class:`WrongRepoSymbolError` if ``symbol`` belongs to
    eta_engine (and therefore must NOT be routed via mnq_bot).

    Symbols not in either set are allowed (the operator can opt-in
    to custom symbols by passing them through mnq_bot). This is
    a *bias-rejection* guard, not a strict allowlist.

    Parameters
    ----------
    symbol:
        Symbol identifier as the runtime would see it. Comparison is
        case-insensitive and tolerates leading slashes (``/MBT`` ->
        ``MBT``).
    """
    norm = symbol.strip().upper().lstrip("/")
    if norm in ETA_ENGINE_SYMBOLS:
        msg = (
            f"symbol {symbol!r} (normalized {norm!r}) is in scope for "
            f"the eta_engine repo, NOT mnq_bot. Per the locked "
            f"two-project decision (CLAUDE.md 2026-04-17), this repo "
            f"trades MNQ on IBKR; CME micro crypto futures (MBT/MET) "
            f"and spot crypto routing are handled by "
            f"eta_engine/venues/cme_micro_crypto.py. "
            f"If you genuinely need to route this symbol via mnq_bot, "
            f"either: (a) wire it through firm_bridge.py instead, or "
            f"(b) edit src/mnq/venues/repo_scope.py::ETA_ENGINE_SYMBOLS "
            f"to remove the guard AND document the boundary change in "
            f"CLAUDE.md."
        )
        raise WrongRepoSymbolError(msg)


def is_in_repo_scope(symbol: str) -> bool:
    """Read-only predicate. True if ``symbol`` is OK for mnq_bot to route.

    Returns False for eta_engine-scope symbols, True otherwise.
    """
    norm = symbol.strip().upper().lstrip("/")
    return norm not in ETA_ENGINE_SYMBOLS


__all__ = [
    "ETA_ENGINE_SYMBOLS",
    "MNQ_BOT_SYMBOLS",
    "WrongRepoSymbolError",
    "assert_symbol_in_repo_scope",
    "is_in_repo_scope",
]
