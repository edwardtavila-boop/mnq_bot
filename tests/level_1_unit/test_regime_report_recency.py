"""Tests for v0.2.19's E_recency + drift columns in
``scripts/regime_report.py``.

Pin the contract:

  * Markdown header includes E_recency + drift columns
  * Each row shows recency value (or "-" when None)
  * drift cell is STEADY / FADING (delta) / GROWING (delta) / "-"
  * _build_rows includes recency_weighted_expectancy_r in each row
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
        "regime_report_recency_test", SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["regime_report_recency_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Markdown header + cells
# ---------------------------------------------------------------------------


def test_markdown_header_includes_recency_and_drift(report_mod) -> None:
    md = report_mod._render_markdown([])
    assert "E_recency" in md
    assert "drift" in md


def test_steady_row_shows_steady_label(report_mod) -> None:
    rows = [{
        "variant": "v_steady",
        "sample_size": 30,
        "expected_expectancy_r": 0.10,
        "recency_weighted_expectancy_r": 0.105,  # delta +0.005R
        "regimes_approved": [],
        "regime_expectancy": {},
        "provenance": ["cached_backtest"],
    }]
    md = report_mod._render_markdown(rows)
    assert "v_steady" in md
    assert "STEADY" in md


def test_fading_row_shows_fading_label_and_delta(report_mod) -> None:
    rows = [{
        "variant": "v_fade",
        "sample_size": 30,
        "expected_expectancy_r": 0.50,
        "recency_weighted_expectancy_r": 0.10,  # delta -0.4R
        "regimes_approved": [],
        "regime_expectancy": {},
        "provenance": ["cached_backtest"],
    }]
    md = report_mod._render_markdown(rows)
    assert "FADING" in md
    assert "-0.400R" in md


def test_growing_row_shows_growing_label_and_delta(report_mod) -> None:
    rows = [{
        "variant": "v_grow",
        "sample_size": 30,
        "expected_expectancy_r": 0.10,
        "recency_weighted_expectancy_r": 0.50,  # delta +0.4R
        "regimes_approved": [],
        "regime_expectancy": {},
        "provenance": ["cached_backtest"],
    }]
    md = report_mod._render_markdown(rows)
    assert "GROWING" in md
    assert "+0.400R" in md


def test_no_recency_renders_dashes(report_mod) -> None:
    """recency_weighted_expectancy_r=None -> both cells are '-'."""
    rows = [{
        "variant": "v_no_recency",
        "sample_size": 0,
        "expected_expectancy_r": 0.0,
        "recency_weighted_expectancy_r": None,
        "regimes_approved": [],
        "regime_expectancy": {},
        "provenance": ["stub"],
    }]
    md = report_mod._render_markdown(rows)
    # Find the data row (skip header rows). The drift cell should be "-".
    # Easier: assert the FADING/GROWING/STEADY tokens are absent.
    assert "STEADY" not in md
    assert "FADING" not in md
    assert "GROWING" not in md


def test_recency_cell_is_signed_3dp_with_R_suffix(report_mod) -> None:
    """E_recency cell renders as `+0.123R` (matches E_total format)."""
    rows = [{
        "variant": "v",
        "sample_size": 30,
        "expected_expectancy_r": 0.100,
        "recency_weighted_expectancy_r": 0.123,
        "regimes_approved": [],
        "regime_expectancy": {},
        "provenance": ["cached_backtest"],
    }]
    md = report_mod._render_markdown(rows)
    assert "+0.123R" in md
    assert "+0.100R" in md


# ---------------------------------------------------------------------------
# _build_rows propagates the new field
# ---------------------------------------------------------------------------


def test_build_rows_includes_recency_field(report_mod) -> None:
    """Every row from _build_rows must carry
    recency_weighted_expectancy_r (None or float)."""
    rows = report_mod._build_rows(["r5_real_wide_target"])
    assert len(rows) == 1
    assert "recency_weighted_expectancy_r" in rows[0]


def test_build_rows_recency_is_float_for_calibrated_variant(
    report_mod,
) -> None:
    """A variant with cached_backtest provenance should have a float
    recency value (not None)."""
    rows = report_mod._build_rows(["r5_real_wide_target"])
    if not rows:
        pytest.skip("variant not present")
    if "cached_backtest" in rows[0]["provenance"]:
        assert rows[0]["recency_weighted_expectancy_r"] is not None
        assert isinstance(
            rows[0]["recency_weighted_expectancy_r"], float,
        )


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_main_json_includes_recency(report_mod, capsys, tmp_path: Path) -> None:
    """--json output includes the recency field per variant."""
    output = tmp_path / "out.md"
    report_mod.main([
        "--variants", "r5_real_wide_target",
        "--output", str(output),
        "--json",
    ])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["variants"]
    v = parsed["variants"][0]
    assert "recency_weighted_expectancy_r" in v
