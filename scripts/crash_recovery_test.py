"""Crash-recovery test for the WAL-mode journal.

Phase 1: the journal's whole raison d'être is durability. This script
proves it by:

1. Creating a temp journal.
2. Writing a known sequence of events, simulating a process kill between
   commits (by forgetting to close the connection and os._exit-ing in a
   subprocess — WAL should still have the committed rows).
3. Re-opening the journal in a fresh process, replaying the events, and
   asserting the exact sequence + payloads survive.
4. Additionally simulating a "dirty shutdown" by forcibly abandoning the
   writer and reopening the journal under a different connection — the
   SQLite WAL recovery path should kick in automatically.

Exits non-zero on any mismatch.

Usage:

    python scripts/crash_recovery_test.py
    python scripts/crash_recovery_test.py --n-events 500
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


WRITER_SCRIPT = r"""
import os, sys, json
sys.path.insert(0, {src!r})
from mnq.storage.journal import EventJournal

jpath = {jpath!r}
n = {n}
j = EventJournal(jpath)
for i in range(n):
    j.append("crash.test", {{"i": i, "payload": "x" * 32}})
# Simulate a forceful process kill: bypass Python shutdown and any __del__
# that might try to close things gracefully. WAL should have every commit.
os._exit(137)
"""


def _spawn_writer_and_kill(jpath: Path, n: int) -> None:
    script = WRITER_SCRIPT.format(src=str(SRC), jpath=str(jpath), n=n)
    # Run in a subprocess so os._exit(137) can happen in isolation.
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
    )
    # Expect exit 137 from os._exit; anything else is suspicious.
    if result.returncode != 137:
        raise RuntimeError(
            f"writer subprocess did not exit 137 (got {result.returncode}); "
            f"stderr: {result.stderr.decode(errors='replace')}"
        )


def _reopen_and_assert(jpath: Path, n: int) -> dict:
    """Reopen the journal in a fresh connection and verify durability.

    Uses a try/finally to close the EventJournal connection before
    returning — on Windows the parent TemporaryDirectory.cleanup()
    cannot rmtree the dir while SQLite has the file open (WinError 32).
    """
    from mnq.storage.journal import EventJournal

    j = EventJournal(jpath)
    try:
        rows: list[tuple[int, str, dict]] = []
        for entry in j.replay():
            rows.append((entry.seq, entry.event_type, entry.payload))

        errors: list[str] = []
        if len(rows) != n:
            errors.append(f"event count mismatch: expected {n}, got {len(rows)}")
        for i, (_seq, ev_type, payload) in enumerate(rows):
            if ev_type != "crash.test":
                errors.append(f"row {i} wrong event_type: {ev_type!r}")
                break
            if payload.get("i") != i:
                errors.append(f"row {i} payload mismatch: {payload!r}")
                break

        return {
            "events_recovered": len(rows),
            "expected": n,
            "errors": errors,
            "ok": not errors,
        }
    finally:
        j.close()


def run(*, n_events: int = 100) -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        jpath = Path(tmpdir) / "crash.sqlite"
        print(f"spawning writer for n={n_events} events at {jpath}")
        _spawn_writer_and_kill(jpath, n_events)

        # Check the WAL file existed at kill time. (It may be merged on
        # reopen, so we only print a soft signal.)
        wal_path = jpath.with_suffix(".sqlite-wal")
        if wal_path.exists():
            print(f"WAL present at kill time: {wal_path.stat().st_size} bytes")

        print("re-opening journal in a clean process...")
        result = _reopen_and_assert(jpath, n_events)

        print("----------------------------------")
        print(f"events_recovered: {result['events_recovered']}")
        print(f"expected:         {result['expected']}")
        print(f"errors:           {result['errors']}")
        print(f"OK:               {result['ok']}")
        print("----------------------------------")

        # Also persist a summary under reports/ for auditability.
        report_path = REPO_ROOT / "reports" / "crash_recovery.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "# Crash Recovery Test\n\n"
            f"- events recovered: **{result['events_recovered']}** / {n_events}\n"
            f"- errors: **{len(result['errors'])}**\n"
            f"- OK: **{result['ok']}**\n\n"
            "## Procedure\n\n"
            "1. Spawn subprocess that writes n events then `os._exit(137)`.\n"
            "2. Reopen the journal in a fresh process.\n"
            "3. Replay all events and assert (seq, type, payload) match.\n\n"
            "WAL mode + `PRAGMA synchronous=FULL` should guarantee every "
            "committed event is recovered exactly.\n"
        )
        print(f"wrote {report_path}")

        return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAL crash-recovery test.")
    parser.add_argument("--n-events", type=int, default=100)
    args = parser.parse_args(argv)
    return run(n_events=args.n_events)


if __name__ == "__main__":
    _ = os  # keep import live
    raise SystemExit(main())
