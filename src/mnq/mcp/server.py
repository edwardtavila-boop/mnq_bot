"""[REAL] MCP server entry point.

`build_server(state, repo)` constructs a FastMCP server with every
read-only tool registered. `serve_stdio(...)` runs the server over
stdio transport — used by `mnq mcp serve --transport stdio`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mnq.mcp.state import (
    ExecutorStateProvider,
    InMemoryExecutorState,
    StrategyRepository,
)
from mnq.mcp.tools.read_only import build_read_only_tools


def build_server(
    state: ExecutorStateProvider | None = None,
    repo: StrategyRepository | None = None,
    *,
    name: str = "mnq-bot",
) -> Any:
    """Build a FastMCP server with every read-only tool registered.

    Separate from `serve_stdio` so unit tests can introspect the server
    object without running the transport loop.
    """
    from mcp.server.fastmcp import FastMCP  # local import to keep cold-start cheap

    state = state or InMemoryExecutorState()
    repo = repo or StrategyRepository.default()

    mcp = FastMCP(name=name)
    for tname, fn, desc in build_read_only_tools(state, repo):
        mcp.add_tool(fn=fn, name=tname, description=desc)
    return mcp


async def serve_stdio(
    state: ExecutorStateProvider | None = None,
    repo: StrategyRepository | None = None,
) -> None:
    """Run an MCP server over stdio. Blocks until the client disconnects."""
    mcp = build_server(state, repo)
    await mcp.run_stdio_async()


def _cli_main() -> None:  # pragma: no cover — exercised via the CLI
    asyncio.run(serve_stdio())


def registered_tool_names(server: Any) -> list[str]:
    """Return the list of registered tool names from a FastMCP server.

    Used by tests — accesses FastMCP's internal tool manager. If the
    FastMCP API changes, update here only.
    """
    tm = getattr(server, "_tool_manager", None)
    if tm is None:
        return []
    tools = getattr(tm, "_tools", None) or getattr(tm, "list_tools", None)
    if callable(tools):
        return [t.name for t in tools()]
    if isinstance(tools, dict):
        return list(tools.keys())
    return []
