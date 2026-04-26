"""Tests for ``scripts/morning_report.py`` -- v0.2.20 consolidated
daily operator status.

Pin the contract:

  * gather_report returns a dict with doctor + variants + drift_watch
    + top_variants keys
  * Each section's renderer handles the unavailable case gracefully
  * Drift threshold is DRIFT_THRESHOLD_R (default 0.05R) and tagging
    is FADING (negative delta) / GROWING (positive delta)
  * Top variants is sorted by E_recency descending
  * Markdown header includes the timestamp
  * --json output omits the rows list (large) and includes summary
    counts
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "morning_report.py"


@pytest.fixture(scope="module")
def morning_mod():
    spec = importlib.util.spec_from_file_location(
        "morning_report_for_test", SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["morning_report_for_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# _gather_drift
# ---------------------------------------------------------------------------


def test_drift_empty_when_no_variants(morning_mod) -> None:
    """Unavailable variants -> empty drift list."""
    assert morning_mod._gather_drift({"available": False}) == []


def test_drift_excludes_variants_with_no_recency(morning_mod) -> None:
    """A variant without recency_weighted_expectancy_r is skipped
    (no signal)."""
    variants = {
        "available": True,
        "rows": [
            {"variant": "v1", "expected_expectancy_r": 0.5,
             "recency_weighted_expectancy_r": None},
            {"variant": "v2", "expected_expectancy_r": 0.5,
             "recency_weighted_expectancy_r": 0.0},  # delta -0.5R
        ],
    }
    drift = morning_mod._gather_drift(variants)
    names = [d["variant"] for d in drift]
    assert "v1" not in names
    assert "v2" in names


def test_drift_threshold_below_skipped(morning_mod) -> None:
    """|delta| < 0.05R is not drift."""
    variants = {
        "available": True,
        "rows": [
            {"variant": "v_steady", "expected_expectancy_r": 0.10,
             "recency_weighted_expectancy_r": 0.13,  # delta +0.03R
             "bucket": "WATCH"},
        ],
    }
    assert morning_mod._gather_drift(variants) == []


def test_drift_fading_tag(morning_mod) -> None:
    variants = {
        "available": True,
        "rows": [
            {"variant": "v_fade", "expected_expectancy_r": 0.50,
             "recency_weighted_expectancy_r": 0.10,  # delta -0.4R
             "bucket": "WATCH"},
        ],
    }
    drift = morning_mod._gather_drift(variants)
    assert len(drift) == 1
    assert drift[0]["tag"] == "FADING"
    assert drift[0]["delta_r"] == pytest.approx(-0.4)


def test_drift_growing_tag(morning_mod) -> None:
    variants = {
        "available": True,
        "rows": [
            {"variant": "v_grow", "expected_expectancy_r": 0.10,
             "recency_weighted_expectancy_r": 0.50,  # delta +0.4R
             "bucket": "WATCH"},
        ],
    }
    drift = morning_mod._gather_drift(variants)
    assert len(drift) == 1
    assert drift[0]["tag"] == "GROWING"


def test_drift_sorted_by_abs_delta_desc(morning_mod) -> None:
    """Biggest absolute drift first."""
    variants = {
        "available": True,
        "rows": [
            {"variant": "small", "expected_expectancy_r": 0.10,
             "recency_weighted_expectancy_r": 0.16,  # +0.06R
             "bucket": "WATCH"},
            {"variant": "huge", "expected_expectancy_r": 0.50,
             "recency_weighted_expectancy_r": -0.10,  # -0.6R
             "bucket": "WATCH"},
            {"variant": "medium", "expected_expectancy_r": 0.10,
             "recency_weighted_expectancy_r": 0.30,  # +0.20R
             "bucket": "WATCH"},
        ],
    }
    drift = morning_mod._gather_drift(variants)
    assert [d["variant"] for d in drift] == ["huge", "medium", "small"]


# ---------------------------------------------------------------------------
# _gather_top_variants
# ---------------------------------------------------------------------------


def test_top_variants_excludes_none_recency(morning_mod) -> None:
    variants = {
        "available": True,
        "rows": [
            {"variant": "v1", "expected_expectancy_r": 0.5,
             "recency_weighted_expectancy_r": None},
            {"variant": "v2", "expected_expectancy_r": 0.1,
             "recency_weighted_expectancy_r": 0.2,
             "bucket": "WATCH"},
        ],
    }
    top = morning_mod._gather_top_variants(variants)
    names = [t["variant"] for t in top]
    assert "v1" not in names
    assert "v2" in names


def test_top_variants_sorted_by_e_recency_desc(morning_mod) -> None:
    variants = {
        "available": True,
        "rows": [
            {"variant": "low", "expected_expectancy_r": 0.0,
             "recency_weighted_expectancy_r": 0.05,
             "bucket": "WATCH"},
            {"variant": "high", "expected_expectancy_r": 0.0,
             "recency_weighted_expectancy_r": 0.50,
             "bucket": "KEEP"},
            {"variant": "mid", "expected_expectancy_r": 0.0,
             "recency_weighted_expectancy_r": 0.20,
             "bucket": "WATCH"},
        ],
    }
    top = morning_mod._gather_top_variants(variants, top_n=10)
    assert [t["variant"] for t in top] == ["high", "mid", "low"]


def test_top_variants_caps_at_top_n(morning_mod) -> None:
    rows = [
        {"variant": f"v{i}", "expected_expectancy_r": 0.0,
         "recency_weighted_expectancy_r": float(10 - i),
         "bucket": "WATCH"}
        for i in range(10)
    ]
    variants = {"available": True, "rows": rows}
    top = morning_mod._gather_top_variants(variants, top_n=3)
    assert len(top) == 3
    assert top[0]["variant"] == "v0"  # highest recency = 10.0
    assert top[2]["variant"] == "v2"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_doctor_section_handles_unavailable(morning_mod) -> None:
    lines = morning_mod._render_doctor_section({
        "available": False, "error": "test error",
    })
    md = "\n".join(lines)
    assert "Doctor" in md
    assert "test error" in md


def test_render_doctor_section_renders_check_table(morning_mod) -> None:
    lines = morning_mod._render_doctor_section({
        "available": True,
        "checks": [
            {"name": "python_version", "status": "ok", "detail": "Python 3.13"},
            {"name": "regime_evidence", "status": "warn", "detail": "no edge"},
        ],
        "n_total": 2, "n_ok": 1, "n_warn": 1, "n_fail": 0,
    })
    md = "\n".join(lines)
    assert "python_version" in md
    assert "OK" in md
    assert "WARN" in md
    assert "**1**" in md  # n_ok


def test_render_doctor_truncates_long_detail(morning_mod) -> None:
    """Detail strings > 80 chars are truncated with '...' so the
    table stays readable."""
    long_detail = "x" * 200
    lines = morning_mod._render_doctor_section({
        "available": True,
        "checks": [
            {"name": "test", "status": "warn", "detail": long_detail},
        ],
        "n_total": 1, "n_ok": 0, "n_warn": 1, "n_fail": 0,
    })
    md = "\n".join(lines)
    # The full 200-char string should NOT appear
    assert long_detail not in md
    assert "..." in md


def test_render_doctor_escapes_pipe_in_detail(morning_mod) -> None:
    """Pipe characters in detail break markdown tables -- they must
    be escaped or stripped."""
    lines = morning_mod._render_doctor_section({
        "available": True,
        "checks": [
            {"name": "x", "status": "warn",
             "detail": "a | b | c"},  # pipe characters
        ],
        "n_total": 1, "n_ok": 0, "n_warn": 1, "n_fail": 0,
    })
    md = "\n".join(lines)
    # The escaped form `\|` should appear instead of bare pipes mid-detail
    assert "a \\| b" in md


def test_render_variants_section_lists_keep_and_watch(morning_mod) -> None:
    variants = {
        "available": True,
        "n_total": 3,
        "n_keep": 1, "n_watch": 1, "n_prune": 1,
        "keep": ["v_keep"],
        "watch": ["v_watch"],
        "prune_sample": ["v_prune"],
    }
    lines = morning_mod._render_variants_section(variants)
    md = "\n".join(lines)
    assert "v_keep" in md
    assert "v_watch" in md
    assert "v_prune" in md
    assert "KEEP=1" in md


def test_render_drift_empty_says_no_drift(morning_mod) -> None:
    lines = morning_mod._render_drift_section([])
    md = "\n".join(lines)
    assert "Drift watch" in md
    assert "No variant" in md


def test_render_drift_table_has_required_columns(morning_mod) -> None:
    drift = [{
        "variant": "v_fade",
        "tag": "FADING",
        "expected_expectancy_r": 0.5,
        "recency_weighted_expectancy_r": 0.1,
        "delta_r": -0.4,
        "bucket": "WATCH",
    }]
    lines = morning_mod._render_drift_section(drift)
    md = "\n".join(lines)
    assert "v_fade" in md
    assert "FADING" in md
    assert "-0.400R" in md


def test_render_top_section_handles_empty(morning_mod) -> None:
    lines = morning_mod._render_top_section([])
    md = "\n".join(lines)
    assert "Top variants" in md
    assert "No variants" in md


def test_render_top_section_includes_rank(morning_mod) -> None:
    top = [
        {"variant": "v1", "expected_expectancy_r": 0.10,
         "recency_weighted_expectancy_r": 0.50, "bucket": "WATCH"},
        {"variant": "v2", "expected_expectancy_r": 0.20,
         "recency_weighted_expectancy_r": 0.30, "bucket": "WATCH"},
    ]
    lines = morning_mod._render_top_section(top)
    md = "\n".join(lines)
    assert "rank" in md.lower() or "1" in md
    assert "v1" in md
    assert "v2" in md


# ---------------------------------------------------------------------------
# render_markdown integration
# ---------------------------------------------------------------------------


def test_render_markdown_has_header_and_all_sections(morning_mod) -> None:
    snap = {
        "generated_at_utc": "2026-04-26T12:00:00+00:00",
        "doctor": {"available": False, "error": "x"},
        "variants": {"available": False, "error": "y"},
        "drift_watch": [],
        "top_variants": [],
    }
    md = morning_mod.render_markdown(snap)
    assert "morning report" in md
    assert "2026-04-26" in md
    assert "## Doctor" in md
    assert "## Variant fleet" in md
    assert "## Drift watch" in md
    assert "## Top variants" in md


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_main_writes_markdown_by_default(
    morning_mod, tmp_path: Path,
) -> None:
    output = tmp_path / "report.md"
    rc = morning_mod.main(["--output", str(output)])
    assert rc == 0
    assert output.exists()
    md = output.read_text(encoding="utf-8")
    assert "morning report" in md


def test_main_json_emits_structured_output(
    morning_mod, capsys, tmp_path: Path,
) -> None:
    rc = morning_mod.main(["--json"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    # Top-level keys
    for key in ("generated_at_utc", "doctor", "variants",
                "drift_watch", "top_variants"):
        assert key in parsed


def test_main_json_strips_variant_rows(morning_mod, capsys) -> None:
    """The variants section in JSON output should omit the heavy
    'rows' field (operator can re-run variant_pruner for that)."""
    morning_mod.main(["--json"])
    parsed = json.loads(capsys.readouterr().out)
    if parsed["variants"].get("available"):
        assert "rows" not in parsed["variants"]
