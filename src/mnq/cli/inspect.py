"""[REAL] ``mnq inspect`` -- runtime diagnostic, no orders placed.

Thin Typer wrapper around ``scripts/run_eta_live.py --inspect`` so
the operator can inspect the runtime's spec_payload + most-recent
tape bar + Firm verdict without remembering the script path.

Like ``mnq morning``, the script is the source of truth and this
module imports it via the importlib spec_from_file_location pattern.

Usage
-----
    mnq inspect                       # default variant + tape
    mnq inspect --variant r5_real_wide_target
    mnq inspect --no-firm-review      # skip the per-bar Firm review
    mnq inspect --no-tape             # no tape bar, no firm verdict

The inspect mode does NOT write to the journal, place orders, or
modify rollout state. It's a read-only diagnostic.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    help="Diagnostic: dump spec_payload + first-bar Firm verdict.",
    no_args_is_help=False,
    invoke_without_command=True,
)


def _load_runtime_script() -> object:
    """Load scripts/run_eta_live.py and return the module."""
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "run_eta_live.py"
    spec = importlib.util.spec_from_file_location(
        "_mnq_cli_inspect_runtime",
        script,
    )
    if spec is None or spec.loader is None:
        msg = f"can't load {script}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_mnq_cli_inspect_runtime"] = module
    spec.loader.exec_module(module)
    return module


@app.callback()
def inspect(
    variant: Annotated[
        str,
        typer.Option(
            "--variant",
            "-v",
            help="Strategy variant name (default: r5_real_wide_target).",
        ),
    ] = "r5_real_wide_target",
    no_firm_review: Annotated[
        bool,
        typer.Option(
            "--no-firm-review",
            help="Skip the per-bar Firm review (faster, no shim load).",
        ),
    ] = False,
    no_tape: Annotated[
        bool,
        typer.Option(
            "--no-tape",
            help="Disable tape replay (no bar, no firm verdict section).",
        ),
    ] = False,
) -> None:
    """Run the runtime in --inspect mode.

    Prints (in order):
      * spec_payload (full JSON dump)
      * drift indicator (E vs recency-weighted, if both present)
      * regime_expectancy (markdown table, sorted by E desc)
      * tape bar (most recent tape entry)
      * firm verdict (PM stage, if firm_review enabled)

    Does NOT enter the tick loop, place orders, or modify journal /
    rollout state. Read-only diagnostic.
    """
    try:
        mod = _load_runtime_script()
    except ImportError as exc:
        typer.echo(f"runtime not available: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    argv: list[str] = [
        "--inspect",
        "--max-bars",
        "1",
        "--variant",
        variant,
    ]
    if no_firm_review:
        argv.append("--no-firm-review")
    if no_tape:
        argv.append("--no-tape")

    # The runtime's main() drives an asyncio loop via _amain.
    rc = asyncio.run(mod._amain(argv))
    if rc != 0:
        raise typer.Exit(code=rc)
