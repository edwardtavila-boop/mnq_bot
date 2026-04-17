"""Tests for the `mnq doctor` CLI subcommand."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from mnq.cli.doctor import app, run_all_checks

runner = CliRunner()


def test_run_all_checks_smoke():
    """All checks should return CheckResult with a valid status."""
    results = run_all_checks(strict=False)
    assert len(results) > 0
    for r in results:
        assert r.status in ("ok", "warn", "fail"), f"bad status: {r.status}"
        assert r.name
        assert isinstance(r.detail, str)


def test_doctor_rich_output_runs():
    """Rich table rendering should not raise."""
    result = runner.invoke(app, [])
    # Exit code depends on env (strict is default off), but invocation must work.
    assert result.exit_code in (0, 1)
    assert "mnq doctor" in result.output


def test_doctor_json_output_is_valid():
    """--json must produce parseable JSON."""
    result = runner.invoke(app, ["--json"])
    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    assert "results" in payload
    assert "failed" in payload
    assert isinstance(payload["results"], list)


def test_doctor_strict_treats_missing_env_as_failure(monkeypatch):
    """Under --strict, missing TV_* env vars should cause non-zero exit."""
    for k in (
        "TV_USERNAME",
        "TV_PASSWORD",
        "TV_APP_ID",
        "TV_APP_VERSION",
        "TV_DEVICE_ID",
        "TV_CID",
        "TV_SEC",
    ):
        monkeypatch.delenv(k, raising=False)
    result = runner.invoke(app, ["--strict", "--json"])
    payload = json.loads(result.output)
    env_row = next(r for r in payload["results"] if r["name"] == "tv_env_vars")
    assert env_row["status"] == "fail"
    assert result.exit_code == 1


def test_doctor_non_strict_warns_on_missing_env(monkeypatch):
    """Without --strict, missing TV_* env vars should be a warning, not a failure."""
    for k in (
        "TV_USERNAME",
        "TV_PASSWORD",
        "TV_APP_ID",
        "TV_APP_VERSION",
        "TV_DEVICE_ID",
        "TV_CID",
        "TV_SEC",
    ):
        monkeypatch.delenv(k, raising=False)
    result = runner.invoke(app, ["--json"])
    payload = json.loads(result.output)
    env_row = next(r for r in payload["results"] if r["name"] == "tv_env_vars")
    assert env_row["status"] == "warn"
