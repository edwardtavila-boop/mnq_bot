"""[REAL] Root Typer app — wired into `mnq = "mnq.cli.main:app"` in pyproject.

Subcommand groups get attached below; each lives in its own module.
"""

from __future__ import annotations

import typer

from mnq.cli import doctor as doctor_cli
from mnq.cli import mcp as mcp_cli
from mnq.cli import morning as morning_cli
from mnq.cli import parity as parity_cli
from mnq.cli import spec as spec_cli
from mnq.cli import venue as venue_cli

app = typer.Typer(
    name="apex",
    help="EVOLUTIONARY TRADING ALGO // Equity Sniper — operational CLI.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(venue_cli.app, name="venue", help="Broker/venue operations.")
app.add_typer(spec_cli.app, name="spec", help="Strategy spec operations.")
app.add_typer(mcp_cli.app, name="mcp", help="MCP server operations.")
app.add_typer(doctor_cli.app, name="doctor", help="Environment & wiring health check.")
app.add_typer(parity_cli.app, name="parity", help="Paper-vs-live parity dashboard.")
app.add_typer(
    morning_cli.app, name="morning",
    help="Consolidated daily operator status (doctor + variants + drift).",
)


@app.command()
def version() -> None:
    """Print the package version."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _version

    try:
        typer.echo(_version("mnq-bot"))
    except PackageNotFoundError:
        typer.echo("0.0.1 (unbuilt)")


if __name__ == "__main__":  # pragma: no cover
    app()
