"""CLI subcommand for parity dashboard."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from mnq.observability.parity import (
    compare_envs,
    render_report,
    summarize_env,
)
from mnq.storage.journal import EventJournal

app = typer.Typer(help="Paper-vs-live parity dashboard.")


@app.command()
def compare(
    paper: Annotated[
        Path,
        typer.Option("--paper", help="Path to paper-trading journal (sqlite)"),
    ],
    live: Annotated[
        Path,
        typer.Option("--live", help="Path to live/shadow-trading journal (sqlite)"),
    ],
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Start datetime (ISO 8601 format, e.g. 2026-01-01T00:00:00Z)",
        ),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option(
            "--until",
            help="End datetime (ISO 8601 format, e.g. 2026-01-31T23:59:59Z)",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON instead of formatted text"),
    ] = False,
    threshold_pnl: Annotated[
        float,
        typer.Option(
            "--threshold-pnl",
            help="Per-trade PnL divergence threshold in dollars",
        ),
    ] = 5.0,
    threshold_wr: Annotated[
        float,
        typer.Option("--threshold-wr", help="Win-rate divergence threshold (0.0–1.0)"),
    ] = 0.05,
    threshold_slip: Annotated[
        float,
        typer.Option(
            "--threshold-slip", help="Slippage divergence threshold in ticks"
        ),
    ] = 0.5,
    n_boot: Annotated[
        int,
        typer.Option("--n-boot", help="Number of bootstrap resamples"),
    ] = 1000,
) -> None:
    """Compare paper and live/shadow trading journals.

    Exit code: 0 if OK (no alerts), 2 if divergent (alerts detected).
    """
    # Parse timestamps
    since_dt: datetime | None = None
    until_dt: datetime | None = None

    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError as e:
            typer.echo(f"Invalid --since datetime: {e}", err=True)
            sys.exit(1)

    if until is not None:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError as e:
            typer.echo(f"Invalid --until datetime: {e}", err=True)
            sys.exit(1)

    # Open journals
    try:
        paper_journal = EventJournal(paper)
        live_journal = EventJournal(live)
    except Exception as e:
        typer.echo(f"Failed to open journals: {e}", err=True)
        sys.exit(1)

    try:
        # Summarize both environments
        paper_summary = summarize_env(
            paper_journal,
            env_label="paper",
            since=since_dt,
            until=until_dt,
        )
        live_summary = summarize_env(
            live_journal,
            env_label="live",
            since=since_dt,
            until=until_dt,
        )

        # Compare
        report = compare_envs(
            paper_summary,
            live_summary,
            divergence_threshold_dollars=threshold_pnl,
            divergence_threshold_wr=threshold_wr,
            divergence_threshold_slip=threshold_slip,
            n_boot=n_boot,
        )

        # Output
        if json_output:
            # Serialize to JSON
            output_dict: dict[str, Any] = {
                "paper": {
                    "env": report.paper.env,
                    "n_trades": report.paper.n_trades,
                    "total_pnl": float(report.paper.total_pnl),
                    "expectancy_dollars": float(report.paper.expectancy_dollars),
                    "win_rate": report.paper.win_rate,
                    "avg_slippage_ticks": report.paper.avg_slippage_ticks,
                    "n_rejected": report.paper.n_rejected,
                },
                "live": {
                    "env": report.live.env,
                    "n_trades": report.live.n_trades,
                    "total_pnl": float(report.live.total_pnl),
                    "expectancy_dollars": float(report.live.expectancy_dollars),
                    "win_rate": report.live.win_rate,
                    "avg_slippage_ticks": report.live.avg_slippage_ticks,
                    "n_rejected": report.live.n_rejected,
                },
                "comparison": {
                    "expectancy_diff_dollars": report.expectancy_diff_dollars,
                    "win_rate_diff": report.win_rate_diff,
                    "slippage_diff_ticks": report.slippage_diff_ticks,
                    "trade_pnl_diff_ci": {
                        "point": report.trade_pnl_diff_ci.point,
                        "lo": report.trade_pnl_diff_ci.lo,
                        "hi": report.trade_pnl_diff_ci.hi,
                        "n": report.trade_pnl_diff_ci.n,
                        "ci_level": report.trade_pnl_diff_ci.ci_level,
                        "n_boot": report.trade_pnl_diff_ci.n_boot,
                    },
                },
                "alerts": report.alerts,
                "is_divergent": report.is_divergent,
            }
            typer.echo(json.dumps(output_dict, indent=2))
        else:
            # Render as text
            rendered = render_report(report)
            typer.echo(rendered)

        # Exit with appropriate code
        sys.exit(2 if report.is_divergent else 0)

    finally:
        paper_journal.close()
        live_journal.close()
