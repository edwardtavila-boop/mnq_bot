"""Tests for OW config loader — Batch 11B."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnq.gauntlet.hard_gate import GauntletHardGateConfig
from mnq.gauntlet.ow_config import build_ow_config, load_ow_config


@pytest.fixture()
def weights_file(tmp_path: Path) -> Path:
    """Create a valid outcome_gate_weights.json for testing."""
    data = {
        "method": "pearson_clamp",
        "n_days": 200,
        "total_pnl": 152.50,
        "gate_weights": {
            "cross_mag": 0.073,
            "session": 0.019,
            "vol_band": 0.009,
            "orderflow": 0.0,
            "regime": 0.0,
            "trend_align": 0.0,
        },
    }
    p = tmp_path / "outcome_gate_weights.json"
    p.write_text(json.dumps(data))
    return p


class TestLoadOwConfig:
    def test_loads_valid_file(self, weights_file: Path) -> None:
        cfg = load_ow_config(weights_file)
        assert isinstance(cfg, GauntletHardGateConfig)
        assert cfg.gate_weights is not None
        # Only gates above min_weight (0.001) should be included
        assert "cross_mag" in cfg.gate_weights
        assert "session" in cfg.gate_weights
        assert "vol_band" in cfg.gate_weights
        # Zero-weight gates excluded
        assert "orderflow" not in cfg.gate_weights
        assert "regime" not in cfg.gate_weights

    def test_fallback_on_missing_file(self) -> None:
        cfg = load_ow_config(Path("/nonexistent/path.json"), fallback_raw=True)
        assert cfg.gate_weights is None  # Falls back to raw pass_rate

    def test_raises_on_missing_file_no_fallback(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            load_ow_config(Path("/nonexistent/path.json"), fallback_raw=False)

    def test_custom_thresholds(self, weights_file: Path) -> None:
        cfg = load_ow_config(weights_file, skip_threshold=0.30, reduce_threshold=0.80)
        assert cfg.skip_threshold == 0.30
        assert cfg.reduce_threshold == 0.80

    def test_min_weight_filter(self, weights_file: Path) -> None:
        # Set min_weight high enough to exclude session and vol_band
        cfg = load_ow_config(weights_file, min_weight=0.05)
        assert cfg.gate_weights is not None
        assert "cross_mag" in cfg.gate_weights
        assert "session" not in cfg.gate_weights
        assert "vol_band" not in cfg.gate_weights

    def test_all_below_min_weight_returns_none(self, weights_file: Path) -> None:
        cfg = load_ow_config(weights_file, min_weight=0.50)
        # All weights are below 0.50, so gate_weights should be None
        assert cfg.gate_weights is None

    def test_invalid_json_fallback(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("not valid json {{{")
        cfg = load_ow_config(p, fallback_raw=True)
        assert cfg.gate_weights is None

    def test_no_critical_gates_in_ow_mode(self, weights_file: Path) -> None:
        cfg = load_ow_config(weights_file)
        assert cfg.critical_gates == frozenset()


class TestBuildOwConfig:
    def test_basic(self) -> None:
        cfg = build_ow_config({"cross_mag": 0.07, "regime": 0.0})
        assert cfg.gate_weights is not None
        assert "cross_mag" in cfg.gate_weights
        assert "regime" not in cfg.gate_weights  # below min_weight

    def test_empty_weights(self) -> None:
        cfg = build_ow_config({})
        assert cfg.gate_weights is None

    def test_all_zero_weights(self) -> None:
        cfg = build_ow_config({"a": 0.0, "b": 0.0})
        assert cfg.gate_weights is None

    def test_custom_thresholds(self) -> None:
        cfg = build_ow_config(
            {"cross_mag": 0.07},
            skip_threshold=0.25,
            reduce_threshold=0.75,
        )
        assert cfg.skip_threshold == 0.25
        assert cfg.reduce_threshold == 0.75
