"""Level-1 unit tests for mnq.features.staleness."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from mnq.features.staleness import FeatureStalenessMonitor, StalenessReport
from mnq.storage.journal import EventJournal
from mnq.storage.schema import FEATURE_STALENESS
from tests.level_1_unit._bars import constant_bars


@pytest.fixture
def temp_journal_path() -> Path:
    """Create a temporary journal for testing.

    Uses ``ignore_cleanup_errors=True`` because tests open ``EventJournal``
    against this path but don't always close the underlying SQLite
    connection -- on Windows the WAL/SHM files stay locked and the
    ``TemporaryDirectory.__exit__`` raises ``PermissionError`` even though
    the test itself passed.
    """
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Path(tmp) / "test.db"


class MockFeature:
    """Mock feature with configurable last_update_bar_ts."""

    def __init__(self, last_update: datetime | None = None) -> None:
        self._last_update_bar_ts = last_update

    @property
    def last_update_bar_ts(self) -> datetime | None:
        return self._last_update_bar_ts

    def set_last_update(self, ts: datetime) -> None:
        self._last_update_bar_ts = ts


class TestStalenessReport:
    def test_can_create_report(self) -> None:
        ts = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
        report = StalenessReport(
            feature_name="ema_fast",
            last_update=ts,
            bars_stale=0,
            is_stale=False,
        )
        assert report.feature_name == "ema_fast"
        assert report.bars_stale == 0
        assert report.is_stale is False


class TestFeatureStalenessMonitor:
    def test_init_with_defaults(self) -> None:
        mon = FeatureStalenessMonitor(timeframe_s=300)
        assert mon.timeframe_s == 300
        assert mon.stale_threshold_bars == 2

    def test_register_feature(self) -> None:
        mon = FeatureStalenessMonitor(timeframe_s=300)
        feature = MockFeature()
        mon.register("test_feature", feature)
        assert "test_feature" in mon._features

    def test_register_feature_without_property_raises(self) -> None:
        mon = FeatureStalenessMonitor(timeframe_s=300)

        class BadFeature:
            pass

        with pytest.raises(AttributeError):
            mon.register("bad", BadFeature())

    def test_freshly_updated_feature_not_stale(self) -> None:
        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
        feature = MockFeature(last_update=now)

        mon = FeatureStalenessMonitor(timeframe_s=300, stale_threshold_bars=2)
        mon.register("fresh", feature)

        reports = mon.check(now)
        assert len(reports) == 1
        assert reports[0].feature_name == "fresh"
        assert reports[0].bars_stale == 0
        assert reports[0].is_stale is False

    def test_feature_stale_after_threshold(self) -> None:
        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
        last_update = now - timedelta(seconds=300 * 3)  # 3 bars ago

        feature = MockFeature(last_update=last_update)
        mon = FeatureStalenessMonitor(timeframe_s=300, stale_threshold_bars=2)
        mon.register("stale", feature)

        reports = mon.check(now)
        assert len(reports) == 1
        assert reports[0].bars_stale == 3
        assert reports[0].is_stale is True

    def test_feature_not_yet_at_threshold(self) -> None:
        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
        last_update = now - timedelta(seconds=300 * 1.5)  # 1.5 bars ago

        feature = MockFeature(last_update=last_update)
        mon = FeatureStalenessMonitor(timeframe_s=300, stale_threshold_bars=2)
        mon.register("almost_stale", feature)

        reports = mon.check(now)
        assert len(reports) == 1
        # 1.5 bars stale, threshold is 2, so not stale yet
        assert reports[0].bars_stale == 1
        assert reports[0].is_stale is False

    def test_never_updated_feature_is_stale(self) -> None:
        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
        feature = MockFeature(last_update=None)

        mon = FeatureStalenessMonitor(timeframe_s=300, stale_threshold_bars=2)
        mon.register("never_updated", feature)

        reports = mon.check(now)
        assert len(reports) == 1
        assert reports[0].is_stale is True

    def test_register_multiple_features_mixed_staleness(self) -> None:
        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)

        fresh_feature = MockFeature(last_update=now)
        stale_feature = MockFeature(last_update=now - timedelta(seconds=300 * 3))

        mon = FeatureStalenessMonitor(timeframe_s=300, stale_threshold_bars=2)
        mon.register("fresh", fresh_feature)
        mon.register("stale", stale_feature)

        reports = mon.check(now)
        assert len(reports) == 2

        fresh_report = next(r for r in reports if r.feature_name == "fresh")
        stale_report = next(r for r in reports if r.feature_name == "stale")

        assert fresh_report.is_stale is False
        assert stale_report.is_stale is True

    def test_worst_staleness_returns_max_bars(self) -> None:
        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)

        feature1 = MockFeature(last_update=now - timedelta(seconds=300 * 2))
        feature2 = MockFeature(last_update=now - timedelta(seconds=300 * 5))

        mon = FeatureStalenessMonitor(timeframe_s=300)
        mon.register("f1", feature1)
        mon.register("f2", feature2)

        worst = mon.worst_staleness(now)
        assert worst == 5

    def test_worst_staleness_with_no_features(self) -> None:
        mon = FeatureStalenessMonitor(timeframe_s=300)
        worst = mon.worst_staleness(datetime.now(UTC))
        assert worst == 0

    def test_journal_emits_feature_staleness_event(
        self, temp_journal_path: Path
    ) -> None:
        journal = EventJournal(temp_journal_path)
        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
        last_update = now - timedelta(seconds=300 * 3)

        feature = MockFeature(last_update=last_update)
        mon = FeatureStalenessMonitor(
            timeframe_s=300,
            stale_threshold_bars=2,
            journal=journal,
        )
        mon.register("stale_feature", feature)

        mon.check(now)

        events = list(journal.replay(event_types=(FEATURE_STALENESS,)))
        assert len(events) == 1
        assert events[0].payload["feature_name"] == "stale_feature"
        assert events[0].payload["age_seconds"] == pytest.approx(900.0, rel=0.01)

    def test_journal_only_emits_for_stale_features(
        self, temp_journal_path: Path
    ) -> None:
        journal = EventJournal(temp_journal_path)
        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)

        fresh = MockFeature(last_update=now)
        stale = MockFeature(last_update=now - timedelta(seconds=300 * 3))

        mon = FeatureStalenessMonitor(
            timeframe_s=300,
            stale_threshold_bars=2,
            journal=journal,
        )
        mon.register("fresh", fresh)
        mon.register("stale", stale)

        mon.check(now)

        events = list(journal.replay(event_types=(FEATURE_STALENESS,)))
        # Only the stale feature should emit
        assert len(events) == 1
        assert events[0].payload["feature_name"] == "stale"


class TestFeatureStalenessWithRealFeatures:
    """Test staleness monitor with actual feature instances."""

    def test_ema_last_update_bar_ts(self) -> None:
        from mnq.features.ema import EMA

        ema = EMA(length=10)
        assert ema.last_update_bar_ts is None

        bars = constant_bars(5, price=100.0)
        for bar in bars:
            ema.update(bar)
            assert ema.last_update_bar_ts == bar.ts

    def test_sma_last_update_bar_ts(self) -> None:
        from mnq.features.sma import SMA

        sma = SMA(length=10)
        assert sma.last_update_bar_ts is None

        bars = constant_bars(5, price=100.0)
        for bar in bars:
            sma.update(bar)
            assert sma.last_update_bar_ts == bar.ts

    def test_atr_last_update_bar_ts(self) -> None:
        from mnq.features.atr import ATR

        atr = ATR(length=14)
        assert atr.last_update_bar_ts is None

        bars = constant_bars(20, price=100.0)
        for bar in bars:
            atr.update(bar)
            assert atr.last_update_bar_ts == bar.ts

    def test_rma_last_update_bar_ts(self) -> None:
        from mnq.features.rma import RMA

        rma = RMA(length=10)
        assert rma.last_update_bar_ts is None

        bars = constant_bars(5, price=100.0)
        for bar in bars:
            rma.update(bar)
            assert rma.last_update_bar_ts == bar.ts

    def test_vwap_last_update_bar_ts(self) -> None:
        from mnq.features.vwap import VWAP

        vwap = VWAP()
        assert vwap.last_update_bar_ts is None

        bars = constant_bars(5, price=100.0)
        for bar in bars:
            vwap.update(bar)
            assert vwap.last_update_bar_ts == bar.ts

    def test_rvol_last_update_bar_ts(self) -> None:
        from mnq.features.rvol import RelativeVolume

        rvol = RelativeVolume(length=20)
        assert rvol.last_update_bar_ts is None

        bars = constant_bars(25, price=100.0)
        for bar in bars:
            rvol.update(bar)
            assert rvol.last_update_bar_ts == bar.ts

    def test_monitor_with_real_ema(self) -> None:
        from mnq.features.ema import EMA

        now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
        ema = EMA(length=10)

        # Manually set last_update for testing
        ema._last_update_bar_ts = now - timedelta(seconds=300 * 3)

        mon = FeatureStalenessMonitor(timeframe_s=300, stale_threshold_bars=2)
        mon.register("ema", ema)

        reports = mon.check(now)
        assert len(reports) == 1
        assert reports[0].is_stale is True
