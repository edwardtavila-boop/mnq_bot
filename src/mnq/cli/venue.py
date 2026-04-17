"""[REAL] `mnq venue ...` CLI commands.

Step 1 subcommands:
    mnq venue tradovate auth-test     — login + print token summary
    mnq venue tradovate list-accounts — login + print accounts table

Requires `.env` with `TV_*` vars (see `.env.example`). Uses `demo` by default.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from mnq.venues.tradovate import (
    AuthError,
    Environment,
    TradovateAuthClient,
    TradovateCreds,
    TradovateRestClient,
    hosts_for,
)

app = typer.Typer(help="Broker/venue commands.", no_args_is_help=True)
tradovate_app = typer.Typer(help="Tradovate-specific operations.", no_args_is_help=True)
app.add_typer(tradovate_app, name="tradovate")


console = Console()


def _load_env() -> dict[str, str]:
    """Minimal .env loader — avoids pulling python-dotenv into the hot path."""
    # If python-dotenv is installed, prefer it for robustness (quoting, etc.)
    dotenv_values: Any = None
    with contextlib.suppress(ImportError):
        from dotenv import dotenv_values  # noqa: F811

    merged: dict[str, str] = dict(os.environ)
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        if dotenv_values is not None:
            for k, v in dotenv_values(env_path).items():
                if v is not None and k not in merged:
                    merged[k] = v
        else:
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in merged:
                    merged[k] = v
    return merged


def _resolve_env(explicit: str | None, env_map: dict[str, str]) -> Environment:
    raw = (explicit or env_map.get("TV_ENV") or "demo").lower()
    return Environment.from_str(raw)


@tradovate_app.command("auth-test")
def auth_test(
    env: Annotated[str | None, typer.Option("--env", help="demo|live (default: from TV_ENV)")] = None,
) -> None:
    """Log in to Tradovate and print the resulting token summary."""
    env_map = _load_env()
    environment = _resolve_env(env, env_map)

    try:
        creds = TradovateCreds.from_env(env_map)
    except ValueError as e:
        console.print(f"[red]credentials error:[/red] {e}")
        raise typer.Exit(code=2) from e

    async def _run() -> None:
        async with httpx.AsyncClient(timeout=15.0) as http:
            auth = TradovateAuthClient(hosts_for(environment), creds, http)
            try:
                token = await auth.login()
            except AuthError as e:
                console.print(f"[red]auth failed:[/red] {e}")
                raise typer.Exit(code=1) from e

            table = Table(title="Tradovate auth OK", show_header=False)
            table.add_row("environment", environment.value)
            table.add_row("user_name", token.user_name)
            table.add_row("user_id", str(token.user_id))
            table.add_row("has_live", str(token.has_live))
            table.add_row("user_status", token.user_status)
            table.add_row("expires_at", token.expires_at.isoformat())
            table.add_row("seconds_until_expiry", f"{token.seconds_until_expiry():.0f}")
            console.print(table)

    asyncio.run(_run())


@tradovate_app.command("list-accounts")
def list_accounts(
    env: Annotated[str | None, typer.Option("--env", help="demo|live (default: from TV_ENV)")] = None,
) -> None:
    """List all accounts visible to the authed Tradovate user."""
    env_map = _load_env()
    environment = _resolve_env(env, env_map)

    try:
        creds = TradovateCreds.from_env(env_map)
    except ValueError as e:
        console.print(f"[red]credentials error:[/red] {e}")
        raise typer.Exit(code=2) from e

    async def _run() -> None:
        async with httpx.AsyncClient(timeout=15.0) as http:
            auth = TradovateAuthClient(hosts_for(environment), creds, http)
            try:
                token = await auth.login()
            except AuthError as e:
                console.print(f"[red]auth failed:[/red] {e}")
                raise typer.Exit(code=1) from e
            rest = TradovateRestClient(hosts_for(environment), lambda: token, http)
            accounts = await rest.list_accounts()

            table = Table(title=f"Tradovate accounts ({environment.value})")
            table.add_column("id", justify="right")
            table.add_column("name")
            table.add_column("type")
            table.add_column("active")
            table.add_column("archived")
            for a in accounts:
                table.add_row(str(a.id), a.name, a.account_type, str(a.active), str(a.archived))
            console.print(table)
            console.print(
                "\n[dim]Set [bold]TV_ACCOUNT_ID[/bold] in .env to the id of the account "
                "you intend to trade (typically your paper/DEMO account).[/dim]"
            )

    asyncio.run(_run())
