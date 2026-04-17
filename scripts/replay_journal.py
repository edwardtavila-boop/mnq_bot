"""Journal determinism replay harness.

Phase 2 of the roadmap: the SQLite event journal must be authoritative. This
script:

1. Opens the journal and replays every event through the production
   ``OrderBook.from_journal`` and ``net_positions_from_journal`` helpers.
2. Asserts deterministic reconstruction — counts, position deltas, and a
   checksum over the ordered event stream.
3. Replays the journal twice and asserts both runs produce the exact same
   ``(n_orders, positions, checksum)`` triple. If they don't, the state
   machine is non-deterministic and we stop here.
4. Writes a replay summary markdown alongside other reports.

This is the foundation under Phase 4 parity work: the moment we can trust
that the journal → in-memory reconstruction is deterministic, we can diff
paper-sim vs live shadow streams byte-for-byte.

Usage:

    python scripts/replay_journal.py
    python scripts/replay_journal.py --journal /path/to/journal.sqlite
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.executor.orders import OrderBook  # noqa: E402
from mnq.executor.reconciler import net_positions_from_journal  # noqa: E402
from mnq.storage.journal import EventJournal  # noqa: E402

DEFAULT_JOURNAL = Path("/sessions/kind-keen-faraday/data/live_sim/journal.sqlite")
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "replay_audit.md"


@dataclass(frozen=True)
class ReplayResult:
    n_events: int
    event_type_counts: dict[str, int]
    checksum: str
    n_orders: int
    positions: dict[str, int]


def _compute_checksum(journal: EventJournal) -> str:
    """Stable SHA-256 over the serialized event stream (seq, type, payload)."""
    h = hashlib.sha256()
    for entry in journal.replay():
        # Stable payload encoding (sorted keys).
        payload_json = json.dumps(entry.payload, sort_keys=True, default=str)
        line = f"{entry.seq}\t{entry.event_type}\t{payload_json}\n"
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def _count_events(journal: EventJournal) -> tuple[int, dict[str, int]]:
    counts: dict[str, int] = {}
    n_total = 0
    for entry in journal.replay():
        counts[entry.event_type] = counts.get(entry.event_type, 0) + 1
        n_total += 1
    return n_total, counts


def replay(journal_path: Path | str = DEFAULT_JOURNAL) -> ReplayResult:
    """Replay the journal once; return the reconstructed state fingerprint."""
    path = Path(journal_path)
    if not path.exists():
        raise FileNotFoundError(f"journal not found: {path}")
    journal = EventJournal(path)

    n_events, counts = _count_events(journal)
    checksum = _compute_checksum(journal)
    book = OrderBook.from_journal(journal)
    positions = net_positions_from_journal(journal)

    return ReplayResult(
        n_events=n_events,
        event_type_counts=counts,
        checksum=checksum,
        n_orders=len(book._orders),  # private field; stable across this repo
        positions=dict(positions),
    )


def assert_deterministic(journal_path: Path | str = DEFAULT_JOURNAL) -> tuple[ReplayResult, ReplayResult]:
    """Replay twice; assert both runs are identical. Raises on mismatch."""
    a = replay(journal_path)
    b = replay(journal_path)
    if a != b:
        raise AssertionError(
            "Journal replay is non-deterministic!\n"
            f"  run A: checksum={a.checksum}, n_orders={a.n_orders}, positions={a.positions}\n"
            f"  run B: checksum={b.checksum}, n_orders={b.n_orders}, positions={b.positions}\n"
        )
    return a, b


def _render(result: ReplayResult, ok: bool, journal_path: Path) -> str:
    lines = ["# Journal Replay Audit", ""]
    lines.append(f"- Journal: `{journal_path}`")
    lines.append(f"- Deterministic two-pass replay: **{'OK' if ok else 'FAILED'}**")
    lines.append(f"- Total events: **{result.n_events}**")
    lines.append(f"- Event checksum (SHA-256): `{result.checksum}`")
    lines.append(f"- Reconstructed orders: **{result.n_orders}**")
    lines.append("")
    lines.append("## Event-type distribution")
    lines.append("")
    lines.append("| Event type | Count |")
    lines.append("|---|---:|")
    for et in sorted(result.event_type_counts):
        lines.append(f"| `{et}` | {result.event_type_counts[et]} |")
    lines.append("")
    lines.append("## Reconstructed positions")
    lines.append("")
    if result.positions:
        lines.append("| Symbol | Net position |")
        lines.append("|---|---:|")
        for sym in sorted(result.positions):
            lines.append(f"| {sym} | {result.positions[sym]} |")
    else:
        lines.append("_flat — all positions reconstructed to zero_")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "* A stable two-pass checksum is the prerequisite for byte-for-byte "
        "parity with a future live shadow stream."
    )
    lines.append(
        "* Non-flat positions after a complete day's replay indicate a "
        "ghost position — investigate the reconciler output immediately."
    )
    lines.append(
        "* If this checksum changes without a schema migration, either an "
        "event was rewritten in place (journal breach) or the event stream "
        "was re-ordered — both are incidents."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Journal replay determinism harness.")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    ok = True
    try:
        a, _ = assert_deterministic(args.journal)
    except AssertionError as exc:
        ok = False
        print(exc, file=sys.stderr)
        a = replay(args.journal)

    md = _render(a, ok, args.journal)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
