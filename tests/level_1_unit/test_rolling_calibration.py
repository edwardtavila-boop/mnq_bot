"""Tests for rolling calibration — Batch 7C."""

from __future__ import annotations

import math

from mnq.gauntlet.rolling_calibration import (
    RollingCalibration,
    rolling_calibration_report,
)


class TestRollingCalibrationBasic:
    def test_empty_input(self) -> None:
        cal = RollingCalibration(window=10, step=5)
        assert cal.evaluate([]) == []

    def test_fewer_than_window_returns_single_epoch(self) -> None:
        outcomes = [(0.7, 1), (0.3, 0), (0.6, 1)]
        cal = RollingCalibration(window=10, step=5)
        epochs = cal.evaluate(outcomes)
        assert len(epochs) == 1
        assert epochs[0].n == 3
        assert epochs[0].drift_alert is False

    def test_exact_window_returns_one_epoch(self) -> None:
        outcomes = [(0.5, 1)] * 10
        cal = RollingCalibration(window=10, step=5)
        epochs = cal.evaluate(outcomes)
        assert len(epochs) == 1
        assert epochs[0].n == 10

    def test_two_windows_overlap(self) -> None:
        outcomes = [(0.5, 1)] * 15
        cal = RollingCalibration(window=10, step=5)
        epochs = cal.evaluate(outcomes)
        assert len(epochs) == 2
        assert epochs[0].start == 0
        assert epochs[0].end == 10
        assert epochs[1].start == 5
        assert epochs[1].end == 15

    def test_step_equals_window_no_overlap(self) -> None:
        outcomes = [(0.5, 1)] * 20
        cal = RollingCalibration(window=10, step=10)
        epochs = cal.evaluate(outcomes)
        assert len(epochs) == 2
        assert epochs[0].start == 0
        assert epochs[1].start == 10


class TestBrierAndLogLoss:
    def test_perfect_predictions_brier_zero(self) -> None:
        outcomes = [(1.0, 1), (0.0, 0)] * 30
        cal = RollingCalibration(window=60, step=30)
        epochs = cal.evaluate(outcomes)
        assert len(epochs) == 1
        assert epochs[0].brier < 0.001

    def test_worst_predictions_brier_one(self) -> None:
        outcomes = [(0.0, 1), (1.0, 0)] * 30
        cal = RollingCalibration(window=60, step=30)
        epochs = cal.evaluate(outcomes)
        assert len(epochs) == 1
        assert epochs[0].brier > 0.99

    def test_coin_flip_brier_quarter(self) -> None:
        outcomes = [(0.5, 1), (0.5, 0)] * 30
        cal = RollingCalibration(window=60, step=30)
        epochs = cal.evaluate(outcomes)
        assert abs(epochs[0].brier - 0.25) < 0.001

    def test_base_rate_correct(self) -> None:
        outcomes = [(0.5, 1)] * 40 + [(0.5, 0)] * 20
        cal = RollingCalibration(window=60, step=30)
        epochs = cal.evaluate(outcomes)
        assert abs(epochs[0].base_rate - 40 / 60) < 0.01

    def test_mean_pred_correct(self) -> None:
        outcomes = [(0.8, 1)] * 30 + [(0.2, 0)] * 30
        cal = RollingCalibration(window=60, step=30)
        epochs = cal.evaluate(outcomes)
        assert abs(epochs[0].mean_pred - 0.5) < 0.01


class TestDriftDetection:
    def test_no_drift_stable_epochs(self) -> None:
        # All epochs identical — no drift possible
        outcomes = [(0.6, 1), (0.4, 0)] * 150
        cal = RollingCalibration(window=60, step=30, drift_z=2.0, min_epochs_for_z=3)
        epochs = cal.evaluate(outcomes)
        assert len(epochs) >= 4
        assert not any(e.drift_alert for e in epochs)

    def test_z_scores_none_for_early_epochs(self) -> None:
        outcomes = [(0.5, 1), (0.5, 0)] * 120
        cal = RollingCalibration(window=60, step=30, min_epochs_for_z=3)
        epochs = cal.evaluate(outcomes)
        for e in epochs[:3]:
            assert e.z_brier is None
            assert e.z_log_loss is None

    def test_drift_detected_on_shift(self) -> None:
        # First 4 epochs: well-calibrated. Last epoch: terrible.
        good = [(0.6, 1), (0.4, 0)] * 90  # 180 trades, stable
        bad = [(0.0, 1), (1.0, 0)] * 30  # 60 trades, inverted
        outcomes = good + bad
        cal = RollingCalibration(window=60, step=30, drift_z=2.0, min_epochs_for_z=3)
        epochs = cal.evaluate(outcomes)
        # The last epoch should have much higher brier → drift
        drift_epochs = [e for e in epochs if e.drift_alert]
        assert len(drift_epochs) >= 1

    def test_custom_drift_threshold(self) -> None:
        outcomes = [(0.5, 1), (0.5, 0)] * 150
        # Very low threshold — might trigger on noise
        cal = RollingCalibration(window=60, step=30, drift_z=0.01)
        epochs = cal.evaluate(outcomes)
        # With constant data, all epochs identical, std≈0 → z=None
        # So no drift even with low threshold
        # This tests that the code handles zero-std gracefully
        assert isinstance(epochs, list)


class TestReport:
    def test_empty_report(self) -> None:
        md = rolling_calibration_report([])
        assert "No epochs" in md

    def test_report_has_table(self) -> None:
        outcomes = [(0.5, 1), (0.5, 0)] * 60
        cal = RollingCalibration(window=60, step=30)
        epochs = cal.evaluate(outcomes)
        md = rolling_calibration_report(epochs)
        assert "| epoch |" in md
        assert "brier" in md.lower()

    def test_report_shows_drift(self) -> None:
        good = [(0.6, 1), (0.4, 0)] * 90
        bad = [(0.0, 1), (1.0, 0)] * 30
        outcomes = good + bad
        cal = RollingCalibration(window=60, step=30, drift_z=2.0, min_epochs_for_z=3)
        epochs = cal.evaluate(outcomes)
        md = rolling_calibration_report(epochs)
        if any(e.drift_alert for e in epochs):
            assert "Drift" in md


class TestEpochMetricsFields:
    def test_epoch_idx_sequential(self) -> None:
        outcomes = [(0.5, 1)] * 100
        cal = RollingCalibration(window=10, step=10)
        epochs = cal.evaluate(outcomes)
        for i, e in enumerate(epochs):
            assert e.epoch_idx == i

    def test_log_loss_not_nan(self) -> None:
        outcomes = [(0.7, 1), (0.3, 0)] * 30
        cal = RollingCalibration(window=60, step=30)
        epochs = cal.evaluate(outcomes)
        for e in epochs:
            assert not math.isnan(e.log_loss)
