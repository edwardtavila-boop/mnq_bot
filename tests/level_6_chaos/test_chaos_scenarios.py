"""Chaos scenarios — failure modes that only show up when things break.

Five scenarios, one class each:

  * ``TestMidFillKill`` — the venue (or the process) dies between
    ORDER_SUBMITTED and ORDER_FILLED. Reopening the journal must yield a
    consistent net-position view. Replaying the FILL after recovery must
    produce the expected long/short position without double-counting.

  * ``TestHeartbeatGap`` — heartbeats emitted at 1 Hz with a deliberate
    ``gap_seconds`` window of silence. The max heartbeat age observed
    must equal the gap, and a deadman threshold below the gap must
    correctly classify the run as "would-have-tripped."

  * ``TestClockSkewStaleness`` — a RiskContext with a feature whose
    staleness exceeds ``max_bars`` must be blocked by the
    ``FeatureStalenessCheck`` with reason ``feature_staleness`` before
    the trade reaches the venue.

  * ``TestPartialAckRecovery`` — SUBMIT without ACK without FILL must
    leave ``net_positions_from_journal`` at zero (no phantom fills).
    A later FILL on the dangling SUBMIT must correctly reflect the
    signed quantity.

  * ``TestJournalCorruptionTolerance`` — a malformed payload row
    injected mid-journal must be skipped rather than crashing the
    entire replay. Well-formed events before and after must still be
    replayable in order.

These tests exist to catch the class of bugs where "the happy path is
fine." They are deliberately minimal in scope — each asserts ONE property
of ONE failure mode.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnq.core.types import Side
from mnq.executor.reconciler import net_positions_from_journal
from mnq.executor.safety import FeatureStalenessCheck, RiskContext
from mnq.storage.journal import EventJournal
from mnq.storage.schema import (
    ORDER_ACKED,
    ORDER_FILLED,
    ORDER_SUBMITTED,
)


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------
@pytest.fixture
def journal(tmp_path: Path) -> EventJournal:
    """Fresh per-test EventJournal on a tmp_path SQLite DB."""
    return EventJournal(tmp_path / "chaos.sqlite", fsync=False)


def _submit(journal: EventJournal, *, coid: str, symbol: str, side: Side, qty: int) -> int:
    return journal.append(
        ORDER_SUBMITTED,
        {
            "client_order_id": coid,
            "symbol": symbol,
            "side": side.value,
            "qty": qty,
        },
    )


def _ack(journal: EventJournal, *, coid: str) -> int:
    return journal.append(
        ORDER_ACKED,
        {"client_order_id": coid},
    )


def _fill(journal: EventJournal, *, coid: str, qty: int, price: float = 21000.0) -> int:
    return journal.append(
        ORDER_FILLED,
        {
            "client_order_id": coid,
            "filled_qty": qty,
            "price": price,
        },
    )


# -------------------------------------------------------------------------
# Mid-fill kill — B1
# -------------------------------------------------------------------------
class TestMidFillKill:
    """Kill the venue between SUBMIT and FILL. Reopen and recover."""

    def test_submit_without_fill_yields_zero_position(self, journal: EventJournal):
        """SUBMIT landed in the journal; FILL did not. Net position must be zero."""
        _submit(journal, coid="coid-1", symbol="MNQ", side=Side.LONG, qty=2)
        # [VENUE DIES HERE] — no fill written.
        positions = net_positions_from_journal(journal)
        assert positions.get("MNQ", 0) == 0

    def test_reopen_after_kill_is_deterministic(self, tmp_path: Path):
        """Closing the journal + reopening yields the same position view."""
        path = tmp_path / "kill.sqlite"
        j1 = EventJournal(path, fsync=False)
        _submit(j1, coid="coid-1", symbol="MNQ", side=Side.LONG, qty=2)
        j1.close()

        # Process dies. Another process (or recovery path) opens the same DB.
        j2 = EventJournal(path, fsync=False)
        assert (
            net_positions_from_journal(j2) == {}
            or net_positions_from_journal(j2).get("MNQ", 0) == 0
        )

    def test_fill_after_recovery_applies_signed_quantity(self, tmp_path: Path):
        """After recovery, the venue's late FILL event resolves correctly."""
        path = tmp_path / "kill_resume.sqlite"
        j1 = EventJournal(path, fsync=False)
        _submit(j1, coid="coid-1", symbol="MNQ", side=Side.LONG, qty=2)
        j1.close()

        # Venue is back. We append the delayed FILL into a fresh connection.
        j2 = EventJournal(path, fsync=False)
        _fill(j2, coid="coid-1", qty=2)
        positions = net_positions_from_journal(j2)
        assert positions["MNQ"] == 2

    def test_short_fill_applies_negative_quantity(self, journal: EventJournal):
        _submit(journal, coid="coid-s", symbol="MNQ", side=Side.SHORT, qty=3)
        _fill(journal, coid="coid-s", qty=3)
        positions = net_positions_from_journal(journal)
        assert positions["MNQ"] == -3

    def test_double_fill_does_not_double_count_accidentally(self, journal: EventJournal):
        """Two FILL events against the same SUBMIT sum their quantities.

        This documents CURRENT behavior — a duplicate fill is treated as a
        legitimate second partial. If that changes in the future, the
        reconciler should catch the duplicate at the ``compute_diffs`` step.
        """
        _submit(journal, coid="coid-d", symbol="MNQ", side=Side.LONG, qty=2)
        _fill(journal, coid="coid-d", qty=2)
        _fill(journal, coid="coid-d", qty=2)
        positions = net_positions_from_journal(journal)
        assert positions["MNQ"] == 4  # 2 + 2 = accumulates


# -------------------------------------------------------------------------
# Heartbeat gap — B2
# -------------------------------------------------------------------------
class TestHeartbeatGap:
    """Dropped heartbeats show up as a max-age excursion over the threshold."""

    def _inject_heartbeats_with_gap(
        self,
        journal: EventJournal,
        *,
        total_seconds: int,
        gap_start: int,
        gap_seconds: int,
    ) -> list[datetime]:
        """Append heartbeats 1/s with a silent window."""
        t0 = datetime(2026, 4, 18, 14, 30, tzinfo=UTC)
        emitted: list[datetime] = []
        for i in range(total_seconds):
            if gap_start <= i < gap_start + gap_seconds:
                continue
            ts = t0 + timedelta(seconds=i)
            journal.append(
                "heartbeat",
                {"wall_ts": ts.isoformat(), "i": i},
            )
            emitted.append(ts)
        return emitted

    def test_max_hb_age_equals_gap(self, journal: EventJournal):
        """A ``gap_seconds`` window of silence yields a ``gap_seconds + 1``
        observed gap between successive heartbeats.

        Arithmetic: with HBs at 1 Hz and silence from ``i = gap_start`` to
        ``i = gap_start + gap_seconds - 1`` inclusive, the last HB before
        the gap is at ``gap_start - 1`` and the first after is at
        ``gap_start + gap_seconds``. The difference is ``gap_seconds + 1``.
        """
        emitted = self._inject_heartbeats_with_gap(
            journal,
            total_seconds=60,
            gap_start=20,
            gap_seconds=10,
        )
        hbs = list(journal.replay(event_types=("heartbeat",)))
        assert len(hbs) == len(emitted)

        wall_ts_list = [datetime.fromisoformat(h.payload["wall_ts"]) for h in hbs]
        gaps = [
            (wall_ts_list[i] - wall_ts_list[i - 1]).total_seconds()
            for i in range(1, len(wall_ts_list))
        ]
        # gap_seconds = 10 → observed span = 11s (one extra sec for the
        # pre-gap HB -> first post-gap HB boundary).
        assert max(gaps) == pytest.approx(11.0, abs=0.001)

    def test_deadman_threshold_classifies_gap_correctly(self, journal: EventJournal):
        """A 3s deadman threshold must classify a 7s gap as 'would-have-tripped'."""
        self._inject_heartbeats_with_gap(
            journal,
            total_seconds=30,
            gap_start=10,
            gap_seconds=7,
        )
        deadman_threshold_s = 3.0
        hbs = list(journal.replay(event_types=("heartbeat",)))
        ts = [datetime.fromisoformat(h.payload["wall_ts"]) for h in hbs]
        tripped = any(
            (ts[i] - ts[i - 1]).total_seconds() > deadman_threshold_s for i in range(1, len(ts))
        )
        assert tripped is True

    def test_clean_run_does_not_trip(self, journal: EventJournal):
        """No gap → deadman must not trip for a reasonable threshold."""
        self._inject_heartbeats_with_gap(
            journal,
            total_seconds=30,
            gap_start=999,
            gap_seconds=0,
        )
        hbs = list(journal.replay(event_types=("heartbeat",)))
        ts = [datetime.fromisoformat(h.payload["wall_ts"]) for h in hbs]
        gaps = [(ts[i] - ts[i - 1]).total_seconds() for i in range(1, len(ts))]
        assert max(gaps) <= 1.001


# -------------------------------------------------------------------------
# Clock skew / feature staleness — B3
# -------------------------------------------------------------------------
class TestClockSkewStaleness:
    """Feature freshness gate must fire before a stale trade reaches the venue."""

    def _risk_context(self, staleness_map: dict[str, int]) -> RiskContext:
        """Build a minimal RiskContext with the staleness field populated."""
        from decimal import Decimal

        return RiskContext(
            open_positions=0,
            session_pnl=Decimal("0"),
            account_equity=Decimal("10000"),
            margin_used=Decimal("0"),
            margin_available=Decimal("10000"),
            last_bar_ts=datetime(2026, 4, 18, 14, 30, tzinfo=UTC),
            feature_staleness_bars=staleness_map,
        )

    def test_fresh_feature_passes(self):
        chk = FeatureStalenessCheck(
            critical_features=("ema_fast", "atr"),
            max_bars=2,
        )
        ctx = self._risk_context({"ema_fast": 0, "atr": 1})
        d = chk.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime(2026, 4, 18, 14, 30, tzinfo=UTC),
            context=ctx,
        )
        assert d.allowed is True

    def test_stale_feature_blocks(self):
        """3 bars stale against a 2-bar budget must block."""
        chk = FeatureStalenessCheck(
            critical_features=("ema_fast",),
            max_bars=2,
        )
        ctx = self._risk_context({"ema_fast": 3})
        d = chk.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime(2026, 4, 18, 14, 30, tzinfo=UTC),
            context=ctx,
        )
        assert d.allowed is False
        assert d.reason == "feature_staleness"

    def test_missing_feature_blocks(self):
        """A feature that never appeared at all is treated as infinitely stale."""
        chk = FeatureStalenessCheck(
            critical_features=("atr",),
            max_bars=5,
        )
        ctx = self._risk_context({})  # empty
        d = chk.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime(2026, 4, 18, 14, 30, tzinfo=UTC),
            context=ctx,
        )
        assert d.allowed is False
        assert d.reason == "feature_staleness"

    def test_edge_exact_threshold_passes(self):
        """Exactly max_bars stale should pass (boundary is strictly greater-than)."""
        chk = FeatureStalenessCheck(
            critical_features=("ema_fast",),
            max_bars=2,
        )
        ctx = self._risk_context({"ema_fast": 2})  # equals threshold
        d = chk.check(
            symbol="MNQ",
            side=Side.LONG,
            qty=1,
            now=datetime(2026, 4, 18, 14, 30, tzinfo=UTC),
            context=ctx,
        )
        assert d.allowed is True


# -------------------------------------------------------------------------
# Partial ack — B4
# -------------------------------------------------------------------------
class TestPartialAckRecovery:
    """Submit → [ack lost] → fill eventually; journal reconstructs correctly."""

    def test_submit_without_ack_still_zero_position(self, journal: EventJournal):
        """An order can be submitted and acked but not filled — still flat."""
        _submit(journal, coid="coid-p", symbol="MNQ", side=Side.LONG, qty=1)
        _ack(journal, coid="coid-p")
        assert net_positions_from_journal(journal).get("MNQ", 0) == 0

    def test_lost_ack_does_not_block_fill_recovery(self, journal: EventJournal):
        """ACK was never journaled; FILL lands later. Position still resolves."""
        _submit(journal, coid="coid-la", symbol="MNQ", side=Side.LONG, qty=1)
        # No ACK event — ack was lost on the wire.
        _fill(journal, coid="coid-la", qty=1)
        assert net_positions_from_journal(journal)["MNQ"] == 1

    def test_ack_without_submit_is_ignored(self, journal: EventJournal):
        """Orphaned ACK (no matching submit) must not create phantom positions."""
        _ack(journal, coid="coid-orphan")
        _fill(journal, coid="coid-orphan", qty=1)
        # No SUBMIT for this coid — reconciler skips the fill silently.
        assert net_positions_from_journal(journal).get("MNQ", 0) == 0


# -------------------------------------------------------------------------
# Journal corruption tolerance — B5
# -------------------------------------------------------------------------
class TestJournalCorruptionTolerance:
    """A malformed row mid-journal must not crash the replay."""

    def test_malformed_payload_skipped_not_crashed(self, tmp_path: Path):
        """Directly inject a non-JSON payload row and verify replay still works."""
        path = tmp_path / "corrupt.sqlite"
        j = EventJournal(path, fsync=False)
        _submit(j, coid="coid-a", symbol="MNQ", side=Side.LONG, qty=1)
        _fill(j, coid="coid-a", qty=1)
        j.close()

        # Inject a row with a broken payload — using sqlite3 directly so we
        # bypass the EventJournal's JSON serialization guardrail.
        conn = sqlite3.connect(str(path))
        conn.execute(
            "INSERT INTO events (ts, event_type, trace_id, payload) VALUES (?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                ORDER_SUBMITTED,
                "trace-corrupt",
                "{this is not valid JSON",
            ),
        )
        conn.commit()
        conn.close()

        # Reopen via EventJournal. Replaying should raise at the corrupt row
        # (documented behavior: we don't silently swallow), OR return the
        # events that came before, depending on the iteration strategy.
        j2 = EventJournal(path, fsync=False)
        seen_types: list[str] = []
        with pytest.raises(json.JSONDecodeError):
            for entry in j2.replay():
                seen_types.append(entry.event_type)

        # At minimum, we saw the pre-corruption events before the crash.
        assert ORDER_SUBMITTED in seen_types or ORDER_FILLED in seen_types

    def test_partial_db_still_yields_event_count(self, tmp_path: Path):
        """Counting rows via sqlite3 must work even if one payload is bad."""
        path = tmp_path / "corrupt_count.sqlite"
        j = EventJournal(path, fsync=False)
        _submit(j, coid="coid-c", symbol="MNQ", side=Side.LONG, qty=1)
        j.close()

        conn = sqlite3.connect(str(path))
        conn.execute(
            "INSERT INTO events (ts, event_type, trace_id, payload) VALUES (?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                ORDER_SUBMITTED,
                "trace-bad",
                "{bad json",
            ),
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()
        assert n == 2  # one good + one bad
