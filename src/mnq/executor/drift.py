"""Live turnover drift monitor: detect anomalies vs gauntlet expectation.

Compares realized live turnover (trades per day) to the statistical
expectation set by the gauntlet gate. Emits a DRIFT_ALERT event when
the z-score exceeds a threshold, indicating a significant deviation
from expected behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytz  # type: ignore[import-untyped]

from mnq.observability.logger import get_logger
from mnq.observability.metrics import drift_z_score
from mnq.storage.journal import EventJournal
from mnq.storage.schema import DRIFT_ALERT, DRIFT_OK, ORDER_FILLED

logger = get_logger(__name__)


@dataclass(frozen=True)
class DriftReport:
    """Report on realized metric drift vs expected."""

    metric: str
    expected_mean: float
    expected_std: float
    realized: float
    z_score: float
    threshold_z: float
    is_anomalous: bool


@dataclass(frozen=True)
class DriftMonitorConfig:
    """Configuration for turnover drift monitoring."""

    metric: str = "trades_per_day"
    lookback_sessions: int = 5
    threshold_z: float = 3.0
    exchange_tz: str = "America/New_York"


class TurnoverDriftMonitor:
    """Monitors realized turnover for drift relative to gauntlet expectation.

    On each call to `.check(now)`:
      1. Replays ORDER_FILLED events from the last N trading days
      2. Computes trades_per_day
      3. Z-scores against the gauntlet-reported expected mean/std
      4. Returns a DriftReport; is_anomalous is True if |z| > threshold
      5. Emits a DRIFT_ALERT event when anomalous
    """

    def __init__(
        self,
        journal: EventJournal,
        *,
        expected_mean: float,
        expected_std: float,
        config: DriftMonitorConfig | None = None,
        calendar: Any | None = None,
    ) -> None:
        """Initialize the drift monitor.

        Args:
            journal: EventJournal instance for replaying events.
            expected_mean: Expected trades_per_day from gauntlet.
            expected_std: Expected std of trades_per_day from gauntlet.
            config: DriftMonitorConfig (default: DriftMonitorConfig()).
            calendar: Optional CMEFuturesCalendar for trading day detection.
                     If provided, lookback_sessions counts trading days.
                     If None, counts calendar days.
        """
        self.journal = journal
        self.expected_mean = expected_mean
        self.expected_std = expected_std
        self.config = config or DriftMonitorConfig()
        self.calendar = calendar

    def days_window(self, now: datetime) -> tuple[datetime, datetime]:
        """Compute the start and end of the lookback window.

        Args:
            now: Current time (UTC or with timezone).

        Returns:
            Tuple of (start_dt, end_dt) in UTC, defining the window
            for replaying ORDER_FILLED events.
        """
        # Normalize to UTC
        now_utc = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

        if self.calendar is None:
            # Simple calendar-day lookback
            start_dt = now_utc - timedelta(days=self.config.lookback_sessions)
            return (start_dt, now_utc)

        # Trading-day lookback: walk backwards from now
        exchange_tz = pytz.timezone(self.config.exchange_tz)
        current_date = now_utc.astimezone(exchange_tz).date()
        n_days_needed = self.config.lookback_sessions
        trading_days_found = 0
        while trading_days_found < n_days_needed:
            if self.calendar.is_trading_day(current_date):
                trading_days_found += 1
            current_date = current_date - timedelta(days=1)

        # current_date is now one day before the first trading day
        # Move back to the first trading day
        current_date = current_date + timedelta(days=1)

        # Convert back to UTC
        start_dt_naive = datetime.combine(current_date, datetime.min.time())
        start_dt = exchange_tz.localize(start_dt_naive).astimezone(UTC)

        return (start_dt, now_utc)

    def check(self, now: datetime) -> DriftReport:
        """Check realized turnover and emit alert if anomalous.

        Args:
            now: Current time. Will be normalized to UTC.

        Returns:
            DriftReport with z_score and is_anomalous flag.
        """
        start_dt, end_dt = self.days_window(now)

        # Replay ORDER_FILLED events in the window and track dates
        exchange_tz = pytz.timezone(self.config.exchange_tz)
        fills: list[dict[str, Any]] = []
        dates_set = set()

        for entry in self.journal.replay(event_types=(ORDER_FILLED,)):
            # Use timestamp from payload if available (for testing/flexibility),
            # otherwise use journal entry timestamp
            payload = entry.payload
            if "ts" in payload and isinstance(payload["ts"], str):
                try:
                    ts = datetime.fromisoformat(payload["ts"])
                    # Ensure it has timezone info
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                except (ValueError, TypeError):
                    ts = entry.ts
            else:
                ts = entry.ts

            # Check if in the window
            if start_dt <= ts <= end_dt:
                fills.append(payload)
                # Track the date in exchange timezone
                ts_local = ts.astimezone(exchange_tz)
                dates_set.add(ts_local.date())

        # Compute realized trades_per_day
        if len(fills) == 0:
            realized = 0.0
        else:
            n_days = len(dates_set) if dates_set else 1
            realized = float(len(fills)) / float(n_days)

        # Compute z-score
        if self.expected_std == 0.0:
            z_score = 0.0
        else:
            z_score = (realized - self.expected_mean) / self.expected_std

        # Determine if anomalous
        is_anomalous = abs(z_score) > self.config.threshold_z

        # Update prometheus metric
        drift_z_score.labels(metric=self.config.metric).set(z_score)

        # Emit event
        if is_anomalous:
            self.journal.append(
                DRIFT_ALERT,
                {
                    "metric": self.config.metric,
                    "expected_mean": self.expected_mean,
                    "expected_std": self.expected_std,
                    "realized": realized,
                    "z_score": z_score,
                    "threshold_z": self.config.threshold_z,
                },
            )
            logger.warning(
                "drift_detected",
                metric=self.config.metric,
                z_score=z_score,
                realized=realized,
                expected_mean=self.expected_mean,
            )
        else:
            self.journal.append(
                DRIFT_OK,
                {
                    "metric": self.config.metric,
                    "realized": realized,
                    "z_score": z_score,
                },
            )

        return DriftReport(
            metric=self.config.metric,
            expected_mean=self.expected_mean,
            expected_std=self.expected_std,
            realized=realized,
            z_score=z_score,
            threshold_z=self.config.threshold_z,
            is_anomalous=is_anomalous,
        )


__all__ = [
    "DriftReport",
    "DriftMonitorConfig",
    "TurnoverDriftMonitor",
]
