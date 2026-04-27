"""[REAL] Feature staleness telemetry and monitoring.

Tracks the last-update timestamp for each registered feature and detects
when any feature has not been updated for longer than a specified threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mnq.storage.journal import EventJournal
from mnq.storage.schema import FEATURE_STALENESS


@dataclass(frozen=True)
class StalenessReport:
    """Report on a feature's staleness."""

    feature_name: str
    last_update: datetime | None
    bars_stale: int  # 0 = fresh, measured in bars of timeframe
    is_stale: bool


class FeatureStalenessMonitor:
    """Tracks last-update ts per registered feature, reports staleness.

    Intended usage:
        mon = FeatureStalenessMonitor(timeframe_s=300)  # 5-min bars
        mon.register("ema_fast", feature_instance)
        ...
        reports = mon.check(now=current_bar_ts)
        for r in reports:
            if r.is_stale:
                # halt trading, etc.
    """

    def __init__(
        self,
        *,
        timeframe_s: float,
        stale_threshold_bars: int = 2,
        journal: EventJournal | None = None,
    ) -> None:
        """Initialize the monitor.

        Args:
            timeframe_s: The timeframe of bars in seconds (e.g., 300 for 5m).
            stale_threshold_bars: Number of bars without update before marking stale.
            journal: Optional EventJournal for durability.
        """
        self.timeframe_s = timeframe_s
        self.stale_threshold_bars = stale_threshold_bars
        self.journal = journal
        self._features: dict[str, Any] = {}

    def register(self, name: str, feature: Any) -> None:
        """Register a feature for staleness tracking.

        Args:
            name: Display name for the feature.
            feature: A feature instance with last_update_bar_ts property.
        """
        if not hasattr(feature, "last_update_bar_ts"):
            raise AttributeError(f"feature {name} does not have last_update_bar_ts property")
        self._features[name] = feature

    def check(self, now: datetime) -> list[StalenessReport]:
        """Check staleness of all registered features.

        Args:
            now: The current bar timestamp.

        Returns:
            List of StalenessReport for each registered feature.
        """
        reports: list[StalenessReport] = []

        for name, feature in self._features.items():
            last_update = feature.last_update_bar_ts

            # Compute bars stale.
            if last_update is None:
                bars_stale_int = 999
                is_stale = True
            else:
                age_s = (now - last_update).total_seconds()
                bars_stale_int = int(age_s / self.timeframe_s)
                is_stale = bars_stale_int >= self.stale_threshold_bars

            report = StalenessReport(
                feature_name=name,
                last_update=last_update,
                bars_stale=bars_stale_int,
                is_stale=is_stale,
            )
            reports.append(report)

            # Emit journal event if stale.
            if is_stale and self.journal is not None:
                age_seconds = (now - last_update).total_seconds() if last_update else -1.0
                payload = {
                    "feature_name": name,
                    "age_seconds": age_seconds,
                }
                self.journal.append(FEATURE_STALENESS, payload)

        return reports

    def worst_staleness(self, now: datetime) -> int:
        """Return the maximum staleness across all features in bars.

        Args:
            now: The current bar timestamp.

        Returns:
            The largest bars_stale value.
        """
        reports = self.check(now)
        if not reports:
            return 0
        return max(r.bars_stale for r in reports)
