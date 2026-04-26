"""Tests for ``scripts/generate_regime_classification.py`` -- v0.2.24
artifact generator for the v0.2.4 promotion gate (gate 6).

Pin the contract:

  * Output JSON has the keys the gate evaluator reads
  * losing_regimes contains regimes with expectancy_r <= 0
  * regimes_winning contains regimes with expectancy_r > 0
  * Threshold is 0.0R (any non-positive regime counts as losing)
  * Variants without backtest data produce empty lists (no crash)
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "generate_regime_classification.py"


@pytest.fixture(scope="module")
def gen_mod():
    spec = importlib.util.spec_from_file_location(
        "generate_regime_classification_for_test", SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_regime_classification_for_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# build_classification (pure)
# ---------------------------------------------------------------------------


def test_build_classification_returns_required_keys(gen_mod) -> None:
    payload = gen_mod.build_classification("r5_real_wide_target")
    required = {
        "variant", "regimes_seen", "regimes_winning",
        "losing_regimes", "regime_expectancy", "_threshold_r",
        "provenance",
    }
    missing = required - set(payload.keys())
    assert not missing, f"missing: {missing}"


def test_build_classification_seen_is_subset_of_winning_plus_losing(
    gen_mod,
) -> None:
    """Every seen regime must be either winning or losing."""
    payload = gen_mod.build_classification("r5_real_wide_target")
    seen = set(payload["regimes_seen"])
    bucket = set(payload["regimes_winning"]) | set(payload["losing_regimes"])
    assert seen == bucket


def test_build_classification_winning_and_losing_disjoint(gen_mod) -> None:
    """A regime can't be in both winning and losing."""
    payload = gen_mod.build_classification("r5_real_wide_target")
    overlap = set(payload["regimes_winning"]) & set(payload["losing_regimes"])
    assert not overlap


def test_build_classification_threshold_is_zero(gen_mod) -> None:
    """Pin: a regime with E=0R is LOSING (the gate's question is
    'has the strategy seen its own failure mode' -- E=0 is a tie,
    which counts)."""
    assert gen_mod.LOSING_THRESHOLD_R == 0.0


def test_unknown_variant_yields_empty_lists(gen_mod) -> None:
    """Variants without backtest data -> no regimes seen."""
    payload = gen_mod.build_classification("does_not_exist_42")
    assert payload["regimes_seen"] == []
    assert payload["regimes_winning"] == []
    assert payload["losing_regimes"] == []


# ---------------------------------------------------------------------------
# Synthetic regime_expectancy (verify bucketing logic)
# ---------------------------------------------------------------------------


def test_bucketing_logic_with_synthetic_payload(gen_mod, monkeypatch) -> None:
    """Force build_spec_payload to return a controlled regime_expectancy
    and verify the bucketing."""
    fake_payload = {
        "regime_expectancy": {
            "winner_pos":   {"n_days": 5.0, "expectancy_r": 0.10},
            "loser_neg":    {"n_days": 3.0, "expectancy_r": -0.20},
            "loser_zero":   {"n_days": 7.0, "expectancy_r": 0.00},
            "no_evidence":  {"n_days": 0.0, "expectancy_r": 0.50},
        },
        "provenance": ["variant_cfg", "cached_backtest"],
    }
    monkeypatch.setattr(
        gen_mod, "build_spec_payload",
        lambda variant: fake_payload,
    )
    payload = gen_mod.build_classification("synthetic")
    # n_days=0 regimes are NOT seen
    assert "no_evidence" not in payload["regimes_seen"]
    # E > 0 -> winning
    assert payload["regimes_winning"] == ["winner_pos"]
    # E <= 0 -> losing (zero counts!)
    assert set(payload["losing_regimes"]) == {"loser_neg", "loser_zero"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_writes_artifact_with_required_keys(
    gen_mod, tmp_path: Path,
) -> None:
    output = tmp_path / "out.json"
    rc = gen_mod.main(["--output", str(output), "--quiet"])
    assert rc == 0
    assert output.exists()
    data = json.loads(output.read_text())
    assert "losing_regimes" in data
    assert "regimes_seen" in data
    assert "regimes_winning" in data


def test_main_default_variant_is_r5(gen_mod, tmp_path: Path) -> None:
    """Default variant is r5_real_wide_target (matches the cached
    backtest's primary calibration target)."""
    output = tmp_path / "out.json"
    gen_mod.main(["--output", str(output), "--quiet"])
    data = json.loads(output.read_text())
    assert data["variant"] == "r5_real_wide_target"


def test_main_variant_flag_overrides(gen_mod, tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    gen_mod.main([
        "--variant", "r4_real_orderflow",
        "--output", str(output), "--quiet",
    ])
    data = json.loads(output.read_text())
    assert data["variant"] == "r4_real_orderflow"


def test_main_quiet_suppresses_stdout(
    gen_mod, tmp_path: Path, capsys,
) -> None:
    output = tmp_path / "out.json"
    gen_mod.main(["--output", str(output), "--quiet"])
    captured = capsys.readouterr()
    assert captured.out == ""


def test_main_verbose_prints_summary(
    gen_mod, tmp_path: Path, capsys,
) -> None:
    output = tmp_path / "out.json"
    gen_mod.main(["--output", str(output)])
    captured = capsys.readouterr()
    assert "wrote" in captured.out
    assert "summary" in captured.out
