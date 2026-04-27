"""Tests for mnq.observability.tolerance_harness — Phase 4 auto-divergence."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from mnq.observability.tolerance_harness import (
    AlertSeverity,
    HarnessState,
    ToleranceHarness,
    ToleranceThresholds,
)

# ── HarnessState persistence ──────────────────────────────────────────


class TestHarnessState:
    def test_load_missing_file_returns_defaults(self):
        state = HarnessState.load(Path("/nonexistent/path"))
        assert state.consecutive_critical == 0
        assert state.total_evaluations == 0

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = HarnessState(consecutive_critical=3, total_evaluations=10)
            state.save(path)

            reloaded = HarnessState.load(path)
            assert reloaded.consecutive_critical == 3
            assert reloaded.total_evaluations == 10

    def test_history_capped_at_20(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = HarnessState()
            state.history = [{"ts": f"t{i}"} for i in range(50)]
            state.save(path)

            data = json.loads(path.read_text())
            assert len(data["history"]) == 20

    def test_load_corrupt_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("not json")
            state = HarnessState.load(path)
            assert state.consecutive_critical == 0


# ── ToleranceThresholds defaults ───────────────────────────────────────


class TestToleranceThresholds:
    def test_defaults_sensible(self):
        th = ToleranceThresholds()
        assert th.pnl_warning < th.pnl_critical
        assert th.wr_warning < th.wr_critical
        assert th.slip_warning < th.slip_critical
        assert th.consecutive_critical_max >= 2
        assert th.min_trades >= 5
        assert th.rolling_window >= 10


# ── ToleranceHarness._classify ─────────────────────────────────────────


class TestClassify:
    def test_ok_when_zero(self):
        harness = ToleranceHarness()
        alert = harness._classify("test", 0.0, 1.0, 2.0)
        assert alert.severity == AlertSeverity.OK

    def test_info_below_warning(self):
        harness = ToleranceHarness()
        alert = harness._classify("test", 0.5, 1.0, 2.0)
        assert alert.severity == AlertSeverity.INFO

    def test_warning_at_threshold(self):
        harness = ToleranceHarness()
        alert = harness._classify("test", 1.0, 1.0, 2.0)
        assert alert.severity == AlertSeverity.WARNING

    def test_critical_at_threshold(self):
        harness = ToleranceHarness()
        alert = harness._classify("test", 2.5, 1.0, 2.0)
        assert alert.severity == AlertSeverity.CRITICAL

    def test_negative_values_use_abs(self):
        harness = ToleranceHarness()
        alert = harness._classify("test", -2.5, 1.0, 2.0)
        assert alert.severity == AlertSeverity.CRITICAL


# ── ToleranceHarness._emit_halt ────────────────────────────────────────


class TestEmitHalt:
    def test_writes_hot_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate_path = Path(tmp) / "gate.json"
            harness = ToleranceHarness(gate_path=gate_path)
            harness._emit_halt("test reason")

            data = json.loads(gate_path.read_text())
            assert data["state"] == "HOT"
            assert "test reason" in data["reason"]
            assert data["source"] == "tolerance_harness"


# ── ToleranceHarness.reset_halt ────────────────────────────────────────


class TestResetHalt:
    def test_resets_state_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            gate_path = Path(tmp) / "gate.json"

            # Simulate a halt state
            gate_path.write_text(json.dumps({"state": "HOT"}))

            harness = ToleranceHarness(
                state_path=state_path,
                gate_path=gate_path,
            )
            harness.state.consecutive_critical = 5
            harness.reset_halt()

            assert harness.state.consecutive_critical == 0
            data = json.loads(gate_path.read_text())
            assert data["state"] == "COLD"
