"""[REAL] `mnq mcp serve ...` CLI command."""
from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer(help="MCP server commands.", no_args_is_help=True)
console = Console()


@app.command("serve")
def serve(
    transport: Annotated[str, typer.Option("--transport", help="stdio (only for now)")] = "stdio",
) -> None:
    """Start the MCP server.

    Currently only `stdio` transport is wired. `--transport http` is
    reserved for a future step once auth is in place.
    """
    t = transport.lower()
    if t != "stdio":
        console.print(f"[red]unsupported transport:[/red] {transport!r} (only 'stdio' is implemented)")
        raise typer.Exit(code=2)

    from mnq.mcp.server import serve_stdio

    asyncio.run(serve_stdio())


@app.command("list-tools")
def list_tools() -> None:
    """List the tools the MCP server would register without starting it."""
    from rich.table import Table

    from mnq.mcp.server import build_server, registered_tool_names

    server = build_server()
    names = registered_tool_names(server)
    table = Table(title="mnq-bot MCP tools")
    table.add_column("#", justify="right")
    table.add_column("name")
    for i, n in enumerate(names, 1):
        table.add_row(str(i), n)
    console.print(table)
