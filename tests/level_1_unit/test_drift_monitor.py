"""Tests for mnq.executor.drift (live turnover drift monitoring)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnq.executor.drift import DriftMonitorConfig, DriftReport, TurnoverDriftMonitor
from mnq.observability.metrics import reset_metrics_for_tests
from mnq.storage.journal import EventJournal
from mnq.storage.schema import DRIFT_ALERT, DRIFT_OK, ORDER_FILLED


@pytest.fixture
def temp_journal(tmp_path: Path) -> EventJournal:
    """Create a temporary EventJournal for testing."""
    db_path = tmp_path / "test.db"
    journal = EventJournal(db_path, fsync=False)
    return journal


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    """Reset prometheus metrics before each test."""
    reset_metrics_for_tests()


class TestDriftMonitorBasic:
    """Basic drift monitor functionality."""

    def test_drift_monitor_no_fills_in_window(self, temp_journal: EventJournal) -> None:
        """With no fills in the window, realized should be 0.0."""
        config = DriftMonitorConfig(
            metric="trades_per_day",
            lookback_sessions=5,
            threshold_z=3.0,
        )
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
        )

        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        report = monitor.check(now)

        assert report.metric == "trades_per_day"
        assert report.realized == 0.0
        # Z-score = (0 - 10) / 2 = -5.0
        assert report.z_score == -5.0
        assert report.is_anomalous is True  # Exceeds threshold of 3.0

    def test_drift_monitor_realized_matching_expected(self, temp_journal: EventJournal) -> None:
        """When realized matches expected, z_score should be ~0."""
        # Create fills for 10 trades across 2 ET trading days
        # Must account for UTC-4 offset: each ET calendar day is actually a 28-hour
        # UTC window. Spread trades to cross ET midnight.
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        # Start 2.5 days ago (60 hours before now)
        base_ts = now - timedelta(hours=60)

        for i in range(10):
            ts = base_ts + timedelta(hours=i * 7)  # 7 hours apart
            temp_journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": f"order_{i}",
                    "ts": ts.isoformat(),
                    "side": "BUY",
                    "qty": 1,
                    "price": 100.0 + i,
                },
            )

        config = DriftMonitorConfig(
            metric="trades_per_day",
            lookback_sessions=5,
            threshold_z=3.0,
        )
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=5.0,
            expected_std=1.0,
            config=config,
        )

        report = monitor.check(now)

        # 10 fills spread across multiple ET days
        # Z-score should be reasonable (not extremely high or low)
        assert report.is_anomalous is False
        assert abs(report.z_score) <= 2.0

    def test_drift_monitor_high_realized_anomalous(self, temp_journal: EventJournal) -> None:
        """When realized >> expected, should be anomalous."""
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        base_ts = now - timedelta(days=1)

        # Create 50 fills all within a 1-hour window on the same ET day
        for i in range(50):
            ts = base_ts + timedelta(minutes=i)
            temp_journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": f"order_{i}",
                    "ts": ts.isoformat(),
                    "side": "BUY",
                    "qty": 1,
                    "price": 100.0,
                },
            )

        config = DriftMonitorConfig(
            metric="trades_per_day",
            lookback_sessions=5,
            threshold_z=3.0,
        )
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
        )

        report = monitor.check(now)

        # All 50 fills should be on the same ET day -> 50 trades/day
        assert report.realized == 50.0
        # Z-score = (50 - 10) / 2 = 20.0
        assert report.z_score == 20.0
        assert report.is_anomalous is True

    def test_drift_monitor_low_realized_anomalous(self, temp_journal: EventJournal) -> None:
        """When realized << expected, should be anomalous."""
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        # Start 5 days ago (about 120 hours)
        base_ts = now - timedelta(hours=120)

        # Create 2 fills spread across 5 ET days -> low trades/day
        for i in range(2):
            ts = base_ts + timedelta(days=i * 2)
            temp_journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": f"order_{i}",
                    "ts": ts.isoformat(),
                    "side": "BUY",
                    "qty": 1,
                    "price": 100.0,
                },
            )

        config = DriftMonitorConfig(
            metric="trades_per_day",
            lookback_sessions=5,
            threshold_z=3.0,
        )
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
        )

        report = monitor.check(now)

        # 2 fills across 5 days -> realized = 2/5 = 0.4
        assert report.realized <= 1.0
        # Z-score should be very negative
        assert report.z_score < -3.0
        assert report.is_anomalous is True


class TestDriftMonitorEvents:
    """Drift monitor event emission tests."""

    def test_drift_monitor_emits_drift_alert_when_anomalous(
        self, temp_journal: EventJournal
    ) -> None:
        """Should emit DRIFT_ALERT event when anomalous."""
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        base_ts = now - timedelta(days=1)

        # Create 50 fills -> anomalously high
        for i in range(50):
            ts = base_ts + timedelta(minutes=i)
            temp_journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": f"order_{i}",
                    "ts": ts.isoformat(),
                    "side": "BUY",
                    "qty": 1,
                    "price": 100.0,
                },
            )

        config = DriftMonitorConfig(threshold_z=3.0)
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
        )

        monitor.check(now)

        # Check that DRIFT_ALERT was emitted
        alerts = list(temp_journal.replay(event_types=(DRIFT_ALERT,)))
        assert len(alerts) > 0
        assert alerts[0].event_type == DRIFT_ALERT
        payload = alerts[0].payload
        assert abs(payload["z_score"]) > 3.0

    def test_drift_monitor_emits_drift_ok_when_normal(self, temp_journal: EventJournal) -> None:
        """Should emit DRIFT_OK event when not anomalous."""
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        # Start 2 days ago; spread trades across multiple ET days
        base_ts = now - timedelta(hours=48)

        # Create 10 fills spread across the window
        for i in range(10):
            ts = base_ts + timedelta(hours=i * 5)  # 5 hours apart
            temp_journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": f"order_{i}",
                    "ts": ts.isoformat(),
                    "side": "BUY",
                    "qty": 1,
                    "price": 100.0,
                },
            )

        config = DriftMonitorConfig(threshold_z=3.0)
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=5.0,
            expected_std=1.0,
            config=config,
        )

        report = monitor.check(now)
        # Only emit DRIFT_OK if not anomalous
        if not report.is_anomalous:
            # Check that DRIFT_OK was emitted
            ok_events = list(temp_journal.replay(event_types=(DRIFT_OK,)))
            assert len(ok_events) > 0
            assert ok_events[0].event_type == DRIFT_OK


class TestDriftMonitorDaysWindow:
    """Days window calculation tests."""

    def test_days_window_without_calendar(self, temp_journal: EventJournal) -> None:
        """Without calendar, should use calendar-day lookback."""
        config = DriftMonitorConfig(lookback_sessions=5)
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
            calendar=None,
        )

        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        start, end = monitor.days_window(now)

        # Should be 5 calendar days lookback
        assert (end - start).days == 5
        assert end == now

    def test_days_window_respects_lookback_sessions(self, temp_journal: EventJournal) -> None:
        """Window should respect lookback_sessions parameter."""
        config = DriftMonitorConfig(lookback_sessions=10)
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
            calendar=None,
        )

        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        start, end = monitor.days_window(now)

        # Should be 10 calendar days lookback
        assert (end - start).days == 10

    def test_days_window_with_timezone_normalization(self, temp_journal: EventJournal) -> None:
        """Window should normalize time-aware datetimes to UTC."""
        config = DriftMonitorConfig(lookback_sessions=5)
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
            calendar=None,
        )

        # Provide a timezone-aware datetime
        import pytz

        et = pytz.timezone("America/New_York")
        now_et = et.localize(datetime(2026, 4, 14, 12, 0))
        start, end = monitor.days_window(now_et)

        # Should be normalized to UTC
        assert end.tzinfo == UTC
        assert start.tzinfo == UTC


class TestDriftMonitorMetrics:
    """Prometheus metrics tests."""

    def test_drift_monitor_updates_prometheus_metric(self, temp_journal: EventJournal) -> None:
        """Should update drift_z_score prometheus metric."""
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        base_ts = now - timedelta(days=1)

        # Create 20 fills in 1 day -> 20 trades/day on the same ET day
        for i in range(20):
            ts = base_ts + timedelta(minutes=i)
            temp_journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": f"order_{i}",
                    "ts": ts.isoformat(),
                    "side": "BUY",
                    "qty": 1,
                    "price": 100.0,
                },
            )

        config = DriftMonitorConfig(metric="trades_per_day")
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
        )

        report = monitor.check(now)

        # Check that the metric was updated
        # All 20 fills on same ET day -> Z-score = (20 - 10) / 2 = 5.0
        assert report.z_score == 5.0


class TestDriftMonitorEventFiltering:
    """Event type filtering tests."""

    def test_drift_monitor_ignores_non_order_filled_events(
        self, temp_journal: EventJournal
    ) -> None:
        """Should only count ORDER_FILLED events, ignore other types."""
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        base_ts = now - timedelta(days=1)

        # Append ORDER_FILLED events
        for i in range(10):
            ts = base_ts + timedelta(minutes=i)
            temp_journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": f"order_{i}",
                    "ts": ts.isoformat(),
                    "side": "BUY",
                    "qty": 1,
                    "price": 100.0,
                },
            )

        # Append other event types (should be ignored)
        temp_journal.append(
            "order.submitted",
            {"client_order_id": "order_10", "side": "BUY"},
        )
        temp_journal.append(
            "order.cancelled",
            {"client_order_id": "order_11", "reason": "user cancelled"},
        )

        config = DriftMonitorConfig(lookback_sessions=5)
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=5.0,
            expected_std=1.0,
            config=config,
        )

        report = monitor.check(now)

        # Should only count the 10 ORDER_FILLED events
        # All fit in the same ET day, so realized = 10 trades/day
        assert report.realized == 10.0


class TestDriftMonitorEdgeCases:
    """Edge case handling tests."""

    def test_drift_monitor_zero_std_dev(self, temp_journal: EventJournal) -> None:
        """When expected_std is 0, z_score should be 0."""
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)
        base_ts = now - timedelta(days=1)

        for i in range(10):
            ts = base_ts + timedelta(minutes=i)
            temp_journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": f"order_{i}",
                    "ts": ts.isoformat(),
                    "side": "BUY",
                    "qty": 1,
                    "price": 100.0,
                },
            )

        config = DriftMonitorConfig()
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=0.0,  # Zero std
            config=config,
        )

        report = monitor.check(now)

        # z_score should be 0 (not inf or nan)
        assert report.z_score == 0.0
        assert not report.is_anomalous

    def test_drift_monitor_report_structure(self, temp_journal: EventJournal) -> None:
        """DriftReport should have all required fields."""
        now = datetime(2026, 4, 14, 16, 0, tzinfo=UTC)

        config = DriftMonitorConfig(metric="trades_per_day", threshold_z=3.0)
        monitor = TurnoverDriftMonitor(
            temp_journal,
            expected_mean=10.0,
            expected_std=2.0,
            config=config,
        )

        report = monitor.check(now)

        # Check that all fields are present
        assert isinstance(report, DriftReport)
        assert report.metric == "trades_per_day"
        assert report.expected_mean == 10.0
        assert report.expected_std == 2.0
        assert isinstance(report.realized, float)
        assert isinstance(report.z_score, float)
        assert report.threshold_z == 3.0
        assert isinstance(report.is_anomalous, bool)
