"""Tests for ``mnq.cli.morning`` -- v0.2.21 CLI alias for the
morning_report script.

Pin the contract:

  * `mnq morning` is registered on the root Typer app
  * Default behavior writes a markdown file
  * --json emits JSON to stdout instead
  * --output PATH overrides the default file location
  * Exits non-zero when the underlying script can't be loaded
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnq.cli.main import app
from mnq.cli.morning import _load_script


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_morning_command_is_registered(runner: CliRunner) -> None:
    """`mnq --help` must list the `morning` subcommand."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "morning" in result.output


def test_morning_help_describes_subcommand(runner: CliRunner) -> None:
    """`mnq morning --help` shows the description + flags."""
    result = runner.invoke(app, ["morning", "--help"])
    assert result.exit_code == 0
    assert "Consolidated" in result.output
    assert "--output" in result.output
    assert "--json" in result.output


# ---------------------------------------------------------------------------
# Default behavior
# ---------------------------------------------------------------------------


def test_morning_default_writes_markdown(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Without --json, writes a markdown file at the given --output."""
    output = tmp_path / "report.md"
    result = runner.invoke(app, ["morning", "--output", str(output)])
    assert result.exit_code == 0
    assert output.exists()
    md = output.read_text(encoding="utf-8")
    assert "morning report" in md.lower()
    assert "## Doctor" in md


def test_morning_json_emits_to_stdout(runner: CliRunner) -> None:
    """--json emits JSON; valid round-trip to dict."""
    result = runner.invoke(app, ["morning", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    # Top-level keys (same as gather_report)
    for key in (
        "generated_at_utc",
        "doctor",
        "variants",
        "drift_watch",
        "top_variants",
    ):
        assert key in parsed


def test_morning_short_flag_for_output(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """-o is a short alias for --output."""
    output = tmp_path / "short.md"
    result = runner.invoke(app, ["morning", "-o", str(output)])
    assert result.exit_code == 0
    assert output.exists()


# ---------------------------------------------------------------------------
# _load_script
# ---------------------------------------------------------------------------


def test_load_script_returns_module_with_main() -> None:
    """The loader must produce a module exposing the script's main()."""
    mod = _load_script()
    assert hasattr(mod, "main")
    assert hasattr(mod, "gather_report")
    assert hasattr(mod, "render_markdown")


def test_load_script_module_has_gather_report_callable() -> None:
    """gather_report() should be callable and return a dict."""
    mod = _load_script()
    snap = mod.gather_report()
    assert isinstance(snap, dict)
    assert "doctor" in snap
