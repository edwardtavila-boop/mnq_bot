"""B5 closure (Red Team review 2026-04-25): broker dormancy enforcement.

Per the operator-wide mandate (`CLAUDE.md`, 2026-04-24): Tradovate is
DORMANT. IBKR + Tastytrade are the active futures brokers in
`eta_engine`. This module ports the same enforcement pattern from
`eta_engine/venues/router.py::DORMANT_BROKERS` so that `mnq_bot` does
not silently accept a configured dormant venue.

The set is the single source of truth for "which brokers are
operationally off-limits right now." Code paths that route an order
should consult this set BEFORE submission and refuse to proceed when
the configured broker name is in the set.

When dormancy lifts (e.g. Tradovate funding clears), the operator
flips this set to `frozenset()` (empty) and recommits. The set
deliberately does NOT pull from a config YAML so that flipping it
requires a code change reviewable in git history.

Cross-repo coupling
-------------------
Keep this module structurally aligned with
`eta_engine/venues/router.py::DORMANT_BROKERS`. If one repo flips
dormancy, both should flip in the same operator action. Future v0.2.x
work could promote this to a shared package; today the two-line
duplication is acceptable.
"""
from __future__ import annotations

# Single source of truth -- one line, code-reviewable in git.
# Tradovate flips to frozenset() when funding clears.
DORMANT_BROKERS: frozenset[str] = frozenset({"tradovate"})


class DormantBrokerError(RuntimeError):
    """Raised when an order-routing path picks a dormant broker.

    The exception text names the broker and points at the dormancy
    set so the operator can grep for the override location.
    """


def assert_broker_active(broker_name: str) -> None:
    """Raise :class:`DormantBrokerError` if ``broker_name`` is dormant.

    Cheap, no I/O. Call from order-routing paths and from
    ``mnq doctor``.

    Parameters
    ----------
    broker_name:
        Lowercase venue identifier (``"tradovate"``, ``"ibkr"``,
        ``"tastytrade"``). Comparison is case-insensitive.
    """
    if broker_name.strip().lower() in DORMANT_BROKERS:
        msg = (
            f"broker {broker_name!r} is in DORMANT_BROKERS and cannot "
            f"route live orders. Override by editing "
            f"src/mnq/venues/dormancy.py::DORMANT_BROKERS, but ONLY "
            f"after confirming the broker is funded + reachable + "
            f"the operator has explicitly cleared the dormancy. "
            f"Current dormant set: {sorted(DORMANT_BROKERS)}."
        )
        raise DormantBrokerError(msg)


def is_broker_dormant(broker_name: str) -> bool:
    """Read-only predicate, no exception. Useful for doctor / dashboard."""
    return broker_name.strip().lower() in DORMANT_BROKERS


__all__ = [
    "DORMANT_BROKERS",
    "DormantBrokerError",
    "assert_broker_active",
    "is_broker_dormant",
]
