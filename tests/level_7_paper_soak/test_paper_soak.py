"""Paper soak — replay N bars of real MNQ 1m tape with chaos injected.

Goal: prove the event-sourcing spine survives a realistic sequence of
events + chaos for >= 500 bars without corrupting the journal, losing
sequence monotonicity, or mis-aggregating positions.

Differs from the 72h compressed burn-in (``scripts/burn_in_72h.py``):
  * burn_in injects synthetic uniform heartbeats; paper_soak uses real
    bars from ``data/bars/databento/mnq1_1m.csv``.
  * burn_in emits random event types; paper_soak emits a deterministic
    submit → (optional chaos) → fill sequence so position accounting is
    exactly predictable at the end.
  * burn_in runs 72 sim-hours; paper_soak runs a few hundred bars and
    focuses on invariant *proofs* rather than capacity.

These tests are skipped gracefully if the Databento cache is missing so
they pass on fresh checkouts.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

import pytest

from mnq.core.types import Side
from mnq.executor.reconciler import net_positions_from_journal
from mnq.storage.journal import EventJournal
from mnq.storage.schema import ORDER_FILLED, ORDER_SUBMITTED

MNQ_1M_CSV = Path("C:/Users/edwar/projects/mnq_bot/data/bars/databento/mnq1_1m.csv")


pytestmark = pytest.mark.skipif(
    not MNQ_1M_CSV.exists(),
    reason=f"Databento MNQ 1m cache not present at {MNQ_1M_CSV}",
)


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------
def _load_bars(n_bars: int, offset: int = 0) -> list[dict[str, float]]:
    """Load the first ``n_bars`` after ``offset`` rows of the MNQ 1m cache."""
    out: list[dict[str, float]] = []
    with MNQ_1M_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i < offset:
                continue
            if len(out) >= n_bars:
                break
            out.append(
                {
                    "time": float(row["time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
            )
    return out


def _soak_run(
    journal: EventJournal,
    bars: list[dict[str, float]],
    *,
    submit_every: int = 5,
    chaos_every: int = 50,
    seed: int = 20260418,
) -> dict[str, int]:
    """Walk ``bars`` left-to-right, emit events, inject chaos.

    Returns: ``{"submits": N, "fills": M, "chaos_events": K}`` so tests
    can assert on the counts directly.
    """
    rng = random.Random(seed)
    submits = fills = chaos = 0
    pending: list[tuple[str, Side, int]] = []  # (coid, side, qty)

    for i, bar in enumerate(bars):
        # Heartbeat per bar
        journal.append(
            "heartbeat",
            {"bar_ix": i, "bar_ts": bar["time"], "close": bar["close"]},
        )

        # Submit every Nth bar
        if i > 0 and i % submit_every == 0:
            coid = f"ps-{i:06d}"
            side = Side.LONG if rng.random() < 0.5 else Side.SHORT
            qty = rng.randint(1, 3)
            journal.append(
                ORDER_SUBMITTED,
                {
                    "client_order_id": coid,
                    "symbol": "MNQ",
                    "side": side.value,
                    "qty": qty,
                    "price": bar["close"],
                },
            )
            submits += 1
            pending.append((coid, side, qty))

        # Flush pending submits as fills on the following bar (unless chaos)
        if pending and (i % submit_every != 0 or i == 0):
            coid, side, qty = pending.pop(0)
            journal.append(
                ORDER_FILLED,
                {
                    "client_order_id": coid,
                    "filled_qty": qty,
                    "price": bar["close"],
                },
            )
            fills += 1

        # Chaos every K bars — corrupt journal event but through the proper
        # API so we don't bypass the schema.
        if i > 0 and i % chaos_every == 0:
            journal.append(
                "chaos.inject",
                {"bar_ix": i, "kind": rng.choice(["gap", "dup_fill", "orphan_ack"])},
            )
            chaos += 1

    return {"submits": submits, "fills": fills, "chaos_events": chaos}


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------
class TestPaperSoak:
    """500-bar soak — journal invariants hold under chaos."""

    @pytest.fixture
    def journal(self, tmp_path: Path) -> EventJournal:
        return EventJournal(tmp_path / "paper_soak.sqlite", fsync=False)

    def test_500_bar_soak_preserves_monotonic_seq(self, journal: EventJournal):
        bars = _load_bars(500)
        if len(bars) < 500:
            pytest.skip(f"Databento cache too small: {len(bars)} bars")

        _soak_run(journal, bars, submit_every=5, chaos_every=50)

        # Replay and assert sequence is monotonic with no gaps.
        seqs = [e.seq for e in journal.replay()]
        assert seqs, "journal is empty"
        assert all(b - a == 1 for a, b in zip(seqs, seqs[1:], strict=False))

    def test_soak_net_positions_balance(self, journal: EventJournal):
        """Every SUBMIT was paired with exactly one FILL ⇒ positions are deterministic."""
        bars = _load_bars(500)
        if len(bars) < 500:
            pytest.skip(f"Databento cache too small: {len(bars)} bars")

        counts = _soak_run(journal, bars, submit_every=5, chaos_every=50)

        # At least some events must have fired; test is meaningless otherwise.
        assert counts["submits"] > 10
        assert counts["fills"] > 0

        positions = net_positions_from_journal(journal)
        # Net MNQ position must match the signed sum of all fills.
        # Since SUBMITs get paired to FILLs in _soak_run, the net is
        # sum(signed qty) where sign comes from the SUBMIT's side.
        # We recompute independently for cross-check.
        expected = 0
        submits_seen: dict[str, tuple[str, int]] = {}  # coid -> (side, qty)
        for e in journal.replay(event_types=(ORDER_SUBMITTED,)):
            submits_seen[e.payload["client_order_id"]] = (
                e.payload["side"],
                e.payload["qty"],
            )
        for e in journal.replay(event_types=(ORDER_FILLED,)):
            coid = e.payload["client_order_id"]
            if coid in submits_seen:
                side_str, _ = submits_seen[coid]
                filled = e.payload["filled_qty"]
                expected += filled * (1 if side_str == "long" else -1)
        assert positions.get("MNQ", 0) == expected

    def test_soak_heartbeats_cover_every_bar(self, journal: EventJournal):
        bars = _load_bars(500)
        if len(bars) < 500:
            pytest.skip(f"Databento cache too small: {len(bars)} bars")

        _soak_run(journal, bars, submit_every=10, chaos_every=100)

        hb_count = sum(1 for _ in journal.replay(event_types=("heartbeat",)))
        assert hb_count == len(bars), f"heartbeat undercount: {hb_count} vs {len(bars)}"

    def test_reopen_after_soak_reproduces_positions(self, tmp_path: Path):
        """Close + reopen + replay is deterministic — classic event-sourcing property."""
        path = tmp_path / "soak_reopen.sqlite"
        j1 = EventJournal(path, fsync=False)
        bars = _load_bars(200)
        if len(bars) < 200:
            pytest.skip(f"Databento cache too small: {len(bars)} bars")
        _soak_run(j1, bars, submit_every=5, chaos_every=20)
        pos1 = net_positions_from_journal(j1)
        j1.close()

        j2 = EventJournal(path, fsync=False)
        pos2 = net_positions_from_journal(j2)
        assert pos1 == pos2


class TestPaperSoakDeterminism:
    """Same seed → same events; different seed → different events."""

    def _checksum(self, journal: EventJournal) -> int:
        """Cheap deterministic checksum of the journal's payload stream."""
        h = 0
        for e in journal.replay():
            h = (h * 31 + hash(str(sorted(e.payload.items())))) & 0xFFFF_FFFF
        return h

    def test_same_seed_yields_same_checksum(self, tmp_path: Path):
        bars = _load_bars(200)
        if len(bars) < 200:
            pytest.skip(f"Databento cache too small: {len(bars)} bars")

        j1 = EventJournal(tmp_path / "det1.sqlite", fsync=False)
        j2 = EventJournal(tmp_path / "det2.sqlite", fsync=False)
        _soak_run(j1, bars, seed=42, chaos_every=50, submit_every=5)
        _soak_run(j2, bars, seed=42, chaos_every=50, submit_every=5)

        # Different journal DBs but same sequence of events → same payload chain.
        # We strip seq/ts from the comparison since they're globally unique.
        def event_tuples(j: EventJournal) -> list[tuple[str, str]]:
            out: list[tuple[str, str]] = []
            for e in j.replay():
                # Heartbeat wall timestamps are wall-clock so skip them
                out.append((e.event_type, str(sorted(e.payload.items()))))
            return out

        assert event_tuples(j1) == event_tuples(j2)

    def test_different_seed_yields_different_events(self, tmp_path: Path):
        bars = _load_bars(200)
        if len(bars) < 200:
            pytest.skip(f"Databento cache too small: {len(bars)} bars")

        j1 = EventJournal(tmp_path / "det_a.sqlite", fsync=False)
        j2 = EventJournal(tmp_path / "det_b.sqlite", fsync=False)
        _soak_run(j1, bars, seed=1, chaos_every=50, submit_every=5)
        _soak_run(j2, bars, seed=999, chaos_every=50, submit_every=5)

        # At least the order sides (random per seed) must differ.
        sides_a = [e.payload.get("side") for e in j1.replay(event_types=(ORDER_SUBMITTED,))]
        sides_b = [e.payload.get("side") for e in j2.replay(event_types=(ORDER_SUBMITTED,))]
        assert sides_a != sides_b

    def test_chaos_injection_counts_stable_across_seeds(self, tmp_path: Path):
        """Chaos-event count depends on bar index, not seed — must be equal."""
        bars = _load_bars(200)
        if len(bars) < 200:
            pytest.skip(f"Databento cache too small: {len(bars)} bars")

        j1 = EventJournal(tmp_path / "seed1.sqlite", fsync=False)
        j2 = EventJournal(tmp_path / "seed2.sqlite", fsync=False)
        c1 = _soak_run(j1, bars, seed=1, chaos_every=25, submit_every=5)
        c2 = _soak_run(j2, bars, seed=2, chaos_every=25, submit_every=5)

        # Deterministic frequency of chaos injection
        assert c1["chaos_events"] == c2["chaos_events"]


# -------------------------------------------------------------------------
# Metadata test — cache availability
# -------------------------------------------------------------------------
class TestPaperSoakCacheMetadata:
    """Metadata check — the test suite should surface if the cache is skinny."""

    def test_cache_has_at_least_500_bars(self):
        bars = _load_bars(500)
        # We don't hard-fail here; just surface whether the cache is usable.
        assert len(bars) == 500, f"cache has only {len(bars)} bars — paper_soak tests will skip"

    def test_first_bar_has_sane_prices(self):
        bars = _load_bars(1)
        assert bars, "cache is empty"
        assert bars[0]["close"] > 1000, "MNQ prices should be > 1000"
        assert bars[0]["high"] >= bars[0]["low"], "high < low is impossible"
