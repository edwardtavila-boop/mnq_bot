"""Tests for ``mnq.cli.inspect`` -- v0.2.22 CLI alias for the
runtime --inspect mode.

Pin the contract:

  * `mnq inspect` is registered on the root Typer app
  * --variant / --no-firm-review / --no-tape options are wired
  * The CLI imports run_eta_live.py via importlib spec
  * Default variant is r5_real_wide_target
"""

from __future__ import annotations

from typer.testing import CliRunner

from mnq.cli.inspect import _load_runtime_script
from mnq.cli.main import app


def test_inspect_command_is_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "inspect" in result.output


def test_inspect_help_describes_subcommand() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["inspect", "--help"])
    assert result.exit_code == 0
    assert "Diagnostic" in result.output
    assert "--variant" in result.output
    assert "--no-firm-review" in result.output
    assert "--no-tape" in result.output


def test_inspect_default_variant_in_help() -> None:
    """Default variant should be visible in --help."""
    runner = CliRunner()
    result = runner.invoke(app, ["inspect", "--help"])
    assert "r5_real_wide_target" in result.output


# ---------------------------------------------------------------------------
# _load_runtime_script
# ---------------------------------------------------------------------------


def test_load_runtime_script_returns_module_with_amain() -> None:
    """The loader must produce a module exposing _amain coroutine."""
    mod = _load_runtime_script()
    assert hasattr(mod, "_amain")


def test_load_runtime_script_module_has_runtime_helpers() -> None:
    """Module exposes the helpers tested elsewhere -- catches drift if
    a future refactor renames them."""
    mod = _load_runtime_script()
    assert hasattr(mod, "_run_inspect")
    assert hasattr(mod, "_format_drift_summary")
    assert hasattr(mod, "_format_regime_table")


# ---------------------------------------------------------------------------
# CLI smoke (light -- the runtime is heavy to invoke)
# ---------------------------------------------------------------------------


def test_inspect_smoke_no_firm_review_no_tape() -> None:
    """End-to-end: invoke `mnq inspect --no-firm-review --no-tape`,
    verify it exits 0 and prints the spec_payload section."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["inspect", "--no-firm-review", "--no-tape"],
    )
    assert result.exit_code == 0
    assert "spec_payload" in result.output


def test_inspect_smoke_runs_for_unknown_variant_falls_back() -> None:
    """An unknown variant still produces output (build_spec_payload
    falls back to a stub-provenance payload)."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "inspect",
            "--no-firm-review",
            "--no-tape",
            "--variant",
            "this_variant_definitely_does_not_exist_42",
        ],
    )
    assert result.exit_code == 0
    # The spec_payload section appears regardless
    assert "spec_payload" in result.output
