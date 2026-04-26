"""Tests for ``scripts/regime_report.py`` -- v0.2.14 per-variant
regime-expectancy reporter.

Pin the contract:

  * Default: writes a markdown file with one row per variant, one
    column per canonical regime
  * --variants filter restricts the row set
  * --json emits machine-readable output instead of markdown
  * Empty cells are formatted as "-"
  * Filled cells show "n=<days>/E=<expectancy_r:+.3f>R"
  * Summary block includes total + provenance + evidence counts
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "regime_report.py"


@pytest.fixture(scope="module")
def report_mod():
    spec = importlib.util.spec_from_file_location(
        "regime_report_for_test", SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["regime_report_for_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Cell formatting
# ---------------------------------------------------------------------------


def test_format_cell_none_yields_dash(report_mod) -> None:
    assert report_mod._format_cell(None) == "-"


def test_format_cell_empty_yields_dash(report_mod) -> None:
    assert report_mod._format_cell({}) == "-"


def test_format_cell_zero_days_yields_dash(report_mod) -> None:
    """A regime with 0 days isn't worth showing."""
    assert report_mod._format_cell({"n_days": 0.0, "expectancy_r": 0.5}) == "-"


def test_format_cell_renders_n_and_e(report_mod) -> None:
    out = report_mod._format_cell({"n_days": 12.0, "expectancy_r": 0.123})
    assert "n=12" in out
    assert "+0.123R" in out


def test_format_cell_negative_expectancy(report_mod) -> None:
    out = report_mod._format_cell({"n_days": 5.0, "expectancy_r": -0.456})
    assert "n=5" in out
    assert "-0.456R" in out


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_markdown_has_header_and_summary(report_mod) -> None:
    rows = [
        {
            "variant": "test_variant",
            "sample_size": 30,
            "expected_expectancy_r": 0.05,
            "regimes_approved": ["low-vol-trend"],
            "regime_expectancy": {
                "low-vol-trend": {
                    "n_days": 10.0, "total_pnl": 100.0,
                    "pnl_per_day": 10.0, "expectancy_r": 0.5,
                },
            },
            "provenance": ["variant_cfg", "baseline_yaml"],
        },
    ]
    md = report_mod._render_markdown(rows)
    assert "# Per-variant regime expectancy" in md
    assert "test_variant" in md
    assert "## Summary" in md


def test_render_markdown_has_all_canonical_regimes_as_columns(
    report_mod,
) -> None:
    """The 10 canonical regime values must each appear as a column."""
    md = report_mod._render_markdown([])
    expected = [
        "low-vol-trend", "low-vol-range", "low-vol-reversal",
        "high-vol-trend", "high-vol-range", "high-vol-reversal",
        "crash", "euphoria", "dead-zone", "transition",
    ]
    for regime in expected:
        assert regime in md, f"missing column: {regime}"


def test_render_markdown_renders_provenance(report_mod) -> None:
    rows = [
        {
            "variant": "v1",
            "sample_size": 10,
            "expected_expectancy_r": 0.1,
            "regimes_approved": [],
            "regime_expectancy": {},
            "provenance": ["variant_cfg", "baseline_yaml", "cached_backtest"],
        },
    ]
    md = report_mod._render_markdown(rows)
    assert "variant_cfg,baseline_yaml,cached_backtest" in md


def test_render_markdown_summary_counts_real_edge(report_mod) -> None:
    """Summary counts variants where ANY regime has E > 0.05R AND n_days >= 5."""
    rows = [
        # variant 1: real edge (E > 0.05R AND n_days >= 5)
        {
            "variant": "winner",
            "sample_size": 30,
            "expected_expectancy_r": 0.1,
            "regimes_approved": ["low-vol-trend"],
            "regime_expectancy": {
                "low-vol-trend": {"n_days": 10.0, "expectancy_r": 0.2},
            },
            "provenance": ["variant_cfg", "baseline_yaml", "cached_backtest"],
        },
        # variant 2: thin sample (E good but n_days < 5)
        {
            "variant": "thin",
            "sample_size": 10,
            "expected_expectancy_r": 0.5,
            "regimes_approved": [],
            "regime_expectancy": {
                "low-vol-trend": {"n_days": 2.0, "expectancy_r": 0.5},
            },
            "provenance": ["variant_cfg"],
        },
        # variant 3: no edge (E < threshold)
        {
            "variant": "no_edge",
            "sample_size": 30,
            "expected_expectancy_r": 0.0,
            "regimes_approved": [],
            "regime_expectancy": {
                "low-vol-trend": {"n_days": 15.0, "expectancy_r": 0.01},
            },
            "provenance": ["variant_cfg"],
        },
    ]
    md = report_mod._render_markdown(rows)
    assert "Total variants reviewed: **3**" in md
    assert "real edge + thick evidence): **1**" in md


# ---------------------------------------------------------------------------
# CLI: --json
# ---------------------------------------------------------------------------


def test_main_json_mode_emits_parseable_json(
    report_mod, capsys, tmp_path: Path,
) -> None:
    """--json should emit valid JSON to stdout, not write the markdown file."""
    output = tmp_path / "out.md"
    rc = report_mod.main([
        "--variants", "r5_real_wide_target",
        "--output", str(output),
        "--json",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert "variants" in parsed
    assert isinstance(parsed["variants"], list)
    if parsed["variants"]:
        v = parsed["variants"][0]
        assert "variant" in v
        assert "regime_expectancy" in v


def test_main_filter_restricts_variants(
    report_mod, capsys, tmp_path: Path,
) -> None:
    """--variants filter limits the output to the named variants."""
    output = tmp_path / "out.md"
    report_mod.main([
        "--variants", "r5_real_wide_target",
        "--output", str(output),
        "--json",
    ])
    parsed = json.loads(capsys.readouterr().out)
    names = {v["variant"] for v in parsed["variants"]}
    assert names == {"r5_real_wide_target"}


def test_main_writes_markdown_by_default(
    report_mod, tmp_path: Path,
) -> None:
    """Without --json, the script writes a markdown file at --output."""
    output = tmp_path / "report.md"
    rc = report_mod.main([
        "--variants", "r5_real_wide_target",
        "--output", str(output),
    ])
    assert rc == 0
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert "Per-variant regime expectancy" in content
    assert "r5_real_wide_target" in content


def test_main_unknown_variant_yields_empty_report(
    report_mod, capsys, tmp_path: Path,
) -> None:
    """A variant filter that matches nothing produces a no-rows report."""
    output = tmp_path / "out.md"
    rc = report_mod.main([
        "--variants", "this_variant_does_not_exist",
        "--output", str(output),
        "--json",
    ])
    # Empty result -> rc=0 and stderr message, not crash
    assert rc == 0


# ---------------------------------------------------------------------------
# Build rows
# ---------------------------------------------------------------------------


def test_build_rows_returns_dicts_with_required_keys(report_mod) -> None:
    """Each row must have variant / sample_size / expected_expectancy_r /
    regimes_approved / regime_expectancy / provenance."""
    rows = report_mod._build_rows(["r5_real_wide_target"])
    assert len(rows) == 1
    required = {
        "variant", "sample_size", "expected_expectancy_r",
        "regimes_approved", "regime_expectancy", "provenance",
    }
    missing = required - set(rows[0].keys())
    assert not missing, f"missing: {missing}"


def test_build_rows_filter_is_exact_match(report_mod) -> None:
    """Variant filter is exact-match only (no prefix matching)."""
    rows = report_mod._build_rows(["r5_real"])
    assert len(rows) == 0
