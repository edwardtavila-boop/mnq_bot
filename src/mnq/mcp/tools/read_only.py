"""[REAL] Read-only MCP tools.

These tools do not modify any state; they expose strategy metadata and
executor telemetry for inspection. Write tools (pause/flatten/cancel)
are deferred to a later step.

`build_read_only_tools(state, repo)` returns a list of `(name, fn, description)`
tuples — pure data — so both the FastMCP registration (`mcp.server.py`)
and unit tests can consume them without coupling to the MCP transport.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mnq.mcp.state import (
    ExecutorStateProvider,
    NotWiredError,
    StrategyRepository,
)


def _safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool function so NotWiredError becomes a structured response."""

    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return {"ok": True, "data": fn(*args, **kwargs)}
        except NotWiredError as e:
            return {"ok": False, "error": "not_wired", "message": str(e)}
        except KeyError as e:
            return {"ok": False, "error": "not_found", "message": str(e)}
        except Exception as e:  # pragma: no cover — reserved for unexpected bugs
            return {"ok": False, "error": "internal", "message": f"{type(e).__name__}: {e}"}

    wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
    return wrapper


def build_read_only_tools(
    state: ExecutorStateProvider,
    repo: StrategyRepository,
) -> list[tuple[str, Callable[..., Any], str]]:
    """Return `(name, fn, description)` triples for every read-only tool."""

    def get_strategy(version: str) -> dict[str, Any]:
        """Return the full spec for the given version (id or semver)."""
        return repo.get(version)

    def list_strategy_versions() -> list[dict[str, str]]:
        """List every spec in specs/strategies/ with id, semver, tier, hash."""
        return repo.list_versions()

    def get_executor_state() -> dict[str, Any]:
        """Return the latest executor state snapshot."""
        return state.get_state()

    def get_session_pnl(spec_hash: str | None = None) -> dict[str, Any]:
        """Return session PnL, optionally filtered by spec_hash."""
        return state.get_session_pnl(spec_hash)

    def get_recent_fills(since: str, spec_hash: str | None = None) -> list[dict[str, Any]]:
        """Return fills at or after `since` (ISO-8601), optionally by spec_hash."""
        return state.get_recent_fills(since, spec_hash)

    def get_risk_utilization() -> dict[str, Any]:
        """Return current session/week/per-trade risk utilization."""
        return state.get_risk_utilization()

    def get_ws_health() -> dict[str, Any]:
        """Return WS connectivity health (reconnect count, last heartbeat, etc.)."""
        return state.get_ws_health()

    def get_open_orders(venue: str) -> list[dict[str, Any]]:
        """Return open orders at a venue."""
        return state.get_open_orders(venue)

    return [
        (
            "get_strategy",
            _safe(get_strategy),
            "Get the full strategy spec for the given version id or semver.",
        ),
        (
            "list_strategy_versions",
            _safe(list_strategy_versions),
            "List all strategies in specs/strategies/.",
        ),
        (
            "get_executor_state",
            _safe(get_executor_state),
            "Return the latest executor state snapshot (bars_processed, position, etc.).",
        ),
        (
            "get_session_pnl",
            _safe(get_session_pnl),
            "Session PnL summary. Optional spec_hash filter.",
        ),
        (
            "get_recent_fills",
            _safe(get_recent_fills),
            "Fills at/after an ISO-8601 timestamp. Optional spec_hash filter.",
        ),
        (
            "get_risk_utilization",
            _safe(get_risk_utilization),
            "Current risk caps usage (per-trade / per-session / per-week / position).",
        ),
        ("get_ws_health", _safe(get_ws_health), "Tradovate WS connectivity health."),
        (
            "get_open_orders",
            _safe(get_open_orders),
            "Open orders at the given venue (`tradovate_paper` | `tradovate_live`).",
        ),
    ]
