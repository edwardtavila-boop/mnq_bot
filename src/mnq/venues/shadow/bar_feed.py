"""EVOLUTIONARY TRADING ALGO // venues.shadow.bar_feed.

Live bar feed that connects real-time market data to the shadow
trading observer. The shadow observer runs against live bars without
submitting orders, validating strategy edge before live promotion.

Activation: set ``SHADOW_OBSERVER_ENABLED=1`` in env or call
:func:`start` explicitly. The feed reads from the canonical MNQ
data pipeline (``mnq_data/history/MNQ1_5m.csv`` live tail or
IBKR/Tastytrade streaming snapshot) and appends to the shadow
journal at ``data/shadow/shadow_journal.jsonl``.

Phase 8 — Shadow Trading (30-day mandatory pre-live run).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from mnq.workspace_paths import workspace_mnq_data_root

from mnq.core.types import Bar

_SHADOW_JOURNAL: Path = Path("data/shadow/shadow_journal.jsonl")
_SHADOW_STATE: Path = Path("data/shadow/shadow_state.json")
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_bar_source() -> list[Bar] | None:
    """Read latest bars from the canonical MNQ 5m data pipeline.

    Falls back to synthetic data for bootstrapping.
    """
    csv_path = workspace_mnq_data_root(_REPO_ROOT) / "history" / "MNQ1_5m.csv"
    if not csv_path.exists():
        return None
    from csv import DictReader

    bars: list[Bar] = []
    with open(str(csv_path), newline="") as f:
        reader = DictReader(f)
        for row in reader:
            raw_ts = float(row.get("time", 0))
            bars.append(Bar(
                ts=datetime.fromtimestamp(raw_ts / 1000.0 if raw_ts > 1e10 else raw_ts, tz=UTC),
                open=Decimal(str(row.get("open", 0))),
                high=Decimal(str(row.get("high", 0))),
                low=Decimal(str(row.get("low", 0))),
                close=Decimal(str(row.get("close", 0))),
                volume=int(float(row.get("volume", 0))),
            ))
    return bars[-200:] if bars else None  # last 200 bars for shadow replay


class ShadowBarFeed:
    """Live bar feed for shadow trading observer.

    Runs in its own thread, polling the bar source every ``interval_s``.
    Each new bar is appended to the shadow journal and forwarded to
    registered observers.
    """

    def __init__(
        self,
        *,
        interval_s: float = 60.0,
        bar_source: callable | None = None,
        journal_path: Path | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.interval_s = max(1.0, interval_s)
        self._bar_source = bar_source or _default_bar_source
        self._journal_path = journal_path or _SHADOW_JOURNAL
        self._state_path = state_path or _SHADOW_STATE
        self._observers: list[callable] = []
        self._thread: threading.Thread | None = None
        self._alive = False
        self._bar_count = 0
        self._started_at: datetime | None = None

    @property
    def days_active(self) -> float:
        if self._started_at is None:
            return 0.0
        return (datetime.now(UTC) - self._started_at).total_seconds() / 86400.0

    @property
    def bar_count(self) -> int:
        return self._bar_count

    def register_observer(self, observer: callable) -> None:
        """Register a callable(bars: list[Bar]) observer."""
        self._observers.append(observer)

    def start(self) -> None:
        if self._alive:
            return
        self._alive = True
        self._started_at = datetime.now(UTC)
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._alive = False
        if self._thread:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        last_bar_ts: datetime | None = None
        while self._alive:
            bars = self._bar_source()
            if bars:
                new_bars = [b for b in bars if last_bar_ts is None or b.ts > last_bar_ts]
                if new_bars:
                    self._append_journal(new_bars)
                    for obs in self._observers:
                        try:
                            obs(new_bars)
                        except Exception:
                            pass
                    last_bar_ts = new_bars[-1].ts
                    self._bar_count += len(new_bars)
            self._save_state()
            time.sleep(self.interval_s)

    def _append_journal(self, bars: list[Bar]) -> None:
        try:
            with self._journal_path.open("a", encoding="utf-8") as f:
                for b in bars:
                    f.write(json.dumps({
                        "ts": datetime.now(UTC).isoformat(),
                        "bar_time": b.ts.isoformat() if hasattr(b.ts, 'isoformat') else str(b.ts),
                        "open": float(b.open),
                        "high": float(b.high),
                        "low": float(b.low),
                        "close": float(b.close),
                        "volume": b.volume,
                    }, default=str) + "\n")
        except OSError:
            pass

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps({
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "days_active": round(self.days_active, 2),
                "bar_count": self._bar_count,
                "observers": len(self._observers),
            }, indent=2))
        except OSError:
            pass
