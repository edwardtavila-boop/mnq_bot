"""[REAL] Environment-aware host URLs for Tradovate.

See `docs/TRADOVATE_NOTES.md` §1 for sourcing. The live URLs are guarded by
Hard Rule 3 — nothing in automated flows resolves to `Environment.LIVE`;
only an explicit human CLI invocation may pass `--env live`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Environment(str, Enum):
    DEMO = "demo"
    LIVE = "live"

    @classmethod
    def from_str(cls, value: str) -> Environment:
        normalized = value.strip().lower()
        if normalized in ("demo", "paper"):
            return cls.DEMO
        if normalized in ("live", "prod", "production"):
            return cls.LIVE
        raise ValueError(f"unknown Tradovate environment: {value!r}")


@dataclass(frozen=True, slots=True)
class Hosts:
    """Resolved host URLs for a given environment."""
    env: Environment
    rest_base: str
    trading_ws: str
    market_data_ws: str


# See docs/TRADOVATE_NOTES.md §1 and §6 item 1 — market-data host split
# follows the JS SDK convention and is flagged to verify at first connect.
_HOSTS: dict[Environment, Hosts] = {
    Environment.DEMO: Hosts(
        env=Environment.DEMO,
        rest_base="https://demo.tradovateapi.com/v1",
        trading_ws="wss://demo.tradovateapi.com/v1/websocket",
        market_data_ws="wss://md-demo.tradovateapi.com/v1/websocket",
    ),
    Environment.LIVE: Hosts(
        env=Environment.LIVE,
        rest_base="https://live.tradovateapi.com/v1",
        trading_ws="wss://live.tradovateapi.com/v1/websocket",
        market_data_ws="wss://md.tradovateapi.com/v1/websocket",
    ),
}


def hosts_for(env: Environment | str) -> Hosts:
    """Return the Hosts for an environment, accepting either the enum or its string value."""
    if isinstance(env, str):
        env = Environment.from_str(env)
    return _HOSTS[env]
