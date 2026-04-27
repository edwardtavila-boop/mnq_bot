"""[REAL] ``mnq morning`` -- consolidated daily operator status.

Thin Typer wrapper around ``scripts/morning_report.py`` so the
operator can run ``mnq morning`` instead of remembering the script
path. The script is the source of truth; this module imports it
via the same sys.path-extension pattern used elsewhere in the CLI
(see ``mnq.cli.doctor::_check_regime_evidence``).

Why a CLI alias
---------------
``scripts/morning_report.py`` works fine as-is. But it requires the
operator to remember the path. ``mnq morning`` makes it discoverable
via ``mnq --help`` alongside the rest of the operational toolkit.

Usage
-----
    mnq morning                            # writes reports/morning_report.md
    mnq morning --output reports/today.md
    mnq morning --json
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    help="Consolidated daily operator status.",
    no_args_is_help=False,
    invoke_without_command=True,
)


def _load_script() -> object:
    """Load scripts/morning_report.py as a fresh module under a
    stable name. Returns the module object."""
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "morning_report.py"
    spec = importlib.util.spec_from_file_location(
        "_mnq_cli_morning_report",
        script,
    )
    if spec is None or spec.loader is None:
        msg = f"can't load {script}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_mnq_cli_morning_report"] = module
    spec.loader.exec_module(module)
    return module


@app.callback()
def morning(
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="output markdown path (default: reports/morning_report.md)",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="emit JSON to stdout instead of writing markdown",
        ),
    ] = False,
) -> None:
    """Run the consolidated morning report.

    Aggregates ``mnq doctor`` + ``scripts/regime_report.py`` +
    ``scripts/variant_pruner.py`` into a single markdown digest.
    """
    try:
        mod = _load_script()
    except ImportError as exc:
        typer.echo(f"morning_report not available: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    argv: list[str] = []
    if json_output:
        argv.append("--json")
    if output is not None:
        argv.extend(["--output", str(output)])

    rc = mod.main(argv)
    if rc != 0:
        raise typer.Exit(code=rc)
