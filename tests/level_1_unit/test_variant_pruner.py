"""Tests for ``scripts/variant_pruner.py`` -- v0.2.15 PRUNE/WATCH/KEEP
classifier.

Pin the contract:

  * Stub-only provenance -> PRUNE
  * No regime with E > 0.05R -> PRUNE
  * Edge in a regime but n_days < 5 -> WATCH
  * Edge in a regime with n_days >= 5 -> KEEP
  * Reasoning string is human-readable and identifies the driving regime
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "variant_pruner.py"


@pytest.fixture(scope="module")
def pruner_mod():
    spec = importlib.util.spec_from_file_location(
        "variant_pruner_for_test", SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["variant_pruner_for_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# classify_variant
# ---------------------------------------------------------------------------


def test_stub_only_provenance_is_prune(pruner_mod) -> None:
    payload = {
        "provenance": ["stub"],
        "regime_expectancy": {},
    }
    bucket, reason = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.PRUNE
    assert "stub" in reason.lower()


def test_no_regime_above_threshold_is_prune(pruner_mod) -> None:
    """All regimes have E <= +0.05R -> PRUNE."""
    payload = {
        "provenance": ["variant_cfg", "baseline_yaml"],
        "regime_expectancy": {
            "low-vol-trend": {"n_days": 10.0, "expectancy_r": 0.01},
            "low-vol-range": {"n_days": 5.0, "expectancy_r": -0.10},
        },
    }
    bucket, reason = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.PRUNE
    assert "expectancy_r" in reason


def test_edge_with_thin_sample_is_watch(pruner_mod) -> None:
    """E > +0.05R but n_days < 5 in every promising regime -> WATCH."""
    payload = {
        "provenance": ["variant_cfg", "baseline_yaml", "cached_backtest"],
        "regime_expectancy": {
            "low-vol-trend": {"n_days": 3.0, "expectancy_r": 0.5},
            "low-vol-range": {"n_days": 10.0, "expectancy_r": 0.01},
        },
    }
    bucket, reason = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.WATCH
    assert "low-vol-trend" in reason
    assert "n=3" in reason


def test_edge_with_thick_sample_is_keep(pruner_mod) -> None:
    """E > +0.05R AND n_days >= 5 -> KEEP."""
    payload = {
        "provenance": ["variant_cfg", "baseline_yaml", "cached_backtest"],
        "regime_expectancy": {
            "low-vol-trend": {"n_days": 12.0, "expectancy_r": 0.3},
        },
    }
    bucket, reason = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.KEEP
    assert "low-vol-trend" in reason
    assert "n=12" in reason


def test_keep_picks_highest_expectancy_regime(pruner_mod) -> None:
    """When multiple regimes qualify for KEEP, the reason names the
    one with the highest expectancy."""
    payload = {
        "provenance": ["cached_backtest"],
        "regime_expectancy": {
            "low-vol-trend": {"n_days": 12.0, "expectancy_r": 0.10},
            "high-vol-range": {"n_days": 8.0, "expectancy_r": 0.50},  # winner
            "low-vol-reversal": {"n_days": 6.0, "expectancy_r": 0.20},
        },
    }
    bucket, reason = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.KEEP
    assert "high-vol-range" in reason


def test_watch_picks_highest_thin_expectancy(pruner_mod) -> None:
    payload = {
        "provenance": ["cached_backtest"],
        "regime_expectancy": {
            "low-vol-trend": {"n_days": 2.0, "expectancy_r": 0.10},
            "low-vol-reversal": {"n_days": 4.0, "expectancy_r": 0.40},  # winner
            "low-vol-range": {"n_days": 100.0, "expectancy_r": 0.01},  # below thresh
        },
    }
    bucket, reason = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.WATCH
    assert "low-vol-reversal" in reason


def test_threshold_boundary_for_n_days(pruner_mod) -> None:
    """n_days exactly 5 -> KEEP (>= 5 inclusive)."""
    payload = {
        "provenance": ["cached_backtest"],
        "regime_expectancy": {
            "low-vol-trend": {"n_days": 5.0, "expectancy_r": 0.10},
        },
    }
    bucket, _ = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.KEEP


def test_threshold_boundary_for_expectancy(pruner_mod) -> None:
    """E exactly +0.05R is NOT > +0.05R -> PRUNE (strict greater-than)."""
    payload = {
        "provenance": ["cached_backtest"],
        "regime_expectancy": {
            "low-vol-trend": {"n_days": 12.0, "expectancy_r": 0.05},
        },
    }
    bucket, _ = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.PRUNE


def test_missing_regime_expectancy_field_is_prune(pruner_mod) -> None:
    """Defensive: missing regime_expectancy field -> PRUNE."""
    payload = {"provenance": ["variant_cfg"]}
    bucket, _ = pruner_mod.classify_variant(payload)
    assert bucket == pruner_mod.PRUNE


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_markdown_has_three_sections(pruner_mod) -> None:
    rows = [
        {"variant": "v1", "bucket": pruner_mod.PRUNE, "reason": "no edge",
         "provenance": ["stub"], "n_total": 0, "expected_expectancy_r": 0.0},
        {"variant": "v2", "bucket": pruner_mod.WATCH, "reason": "thin",
         "provenance": ["cached_backtest"], "n_total": 30,
         "expected_expectancy_r": 0.5},
        {"variant": "v3", "bucket": pruner_mod.KEEP, "reason": "edge",
         "provenance": ["cached_backtest"], "n_total": 30,
         "expected_expectancy_r": 0.5},
    ]
    md = pruner_mod._render_markdown(rows)
    assert "## PRUNE" in md
    assert "## WATCH" in md
    assert "## KEEP" in md


def test_render_markdown_empty_bucket_says_none(pruner_mod) -> None:
    rows = [
        {"variant": "v1", "bucket": pruner_mod.PRUNE, "reason": "no edge",
         "provenance": ["stub"], "n_total": 0, "expected_expectancy_r": 0.0},
    ]
    md = pruner_mod._render_markdown(rows)
    # WATCH and KEEP are empty; render "_(none)_" placeholder
    assert md.count("_(none)_") == 2


def test_render_markdown_summary_counts_buckets(pruner_mod) -> None:
    rows = [
        {"variant": "a", "bucket": pruner_mod.PRUNE, "reason": "x",
         "provenance": ["stub"], "n_total": 0, "expected_expectancy_r": 0.0},
        {"variant": "b", "bucket": pruner_mod.PRUNE, "reason": "x",
         "provenance": ["stub"], "n_total": 0, "expected_expectancy_r": 0.0},
        {"variant": "c", "bucket": pruner_mod.KEEP, "reason": "x",
         "provenance": ["cached_backtest"], "n_total": 30,
         "expected_expectancy_r": 0.5},
    ]
    md = pruner_mod._render_markdown(rows)
    assert "PRUNE: **2**" in md
    assert "WATCH: **0**" in md
    assert "KEEP:  **1**" in md


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_writes_markdown_by_default(
    pruner_mod, tmp_path: Path, monkeypatch,
) -> None:
    """Default CLI run produces a markdown file at --output."""
    output = tmp_path / "prune.md"
    rc = pruner_mod.main(["--output", str(output)])
    assert rc == 0
    assert output.exists()
    md = output.read_text(encoding="utf-8")
    assert "# Variant pruner" in md


def test_main_json_mode_emits_summary(pruner_mod, capsys) -> None:
    """--json includes a summary block with PRUNE/WATCH/KEEP counts."""
    rc = pruner_mod.main(["--json"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert "summary" in parsed
    assert "total" in parsed["summary"]
    assert parsed["summary"]["total"] >= 1


def test_main_bucket_filter_lists_names_only(pruner_mod, capsys) -> None:
    """--bucket prints just one bucket's variant names, one per line.
    Useful for piping to xargs."""
    pruner_mod.main(["--bucket", pruner_mod.PRUNE])
    out = capsys.readouterr().out
    # Should be one variant name per line, no markdown
    assert "## PRUNE" not in out
    assert "|" not in out  # no markdown table
    # If there are PRUNE variants in the cached backtest, output is non-empty
    if out.strip():
        for line in out.strip().split("\n"):
            assert line  # non-empty line
