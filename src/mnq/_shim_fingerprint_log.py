"""Append-only log of firm-bridge contract fingerprints over time.

Companion to ``_shim_probe.py``. The probe answers "does the live
contract match the locked checksum NOW?". This log answers "when did
the live contract last change?".

Each row records:
    ts                ISO-8601 UTC
    locked_checksum   what the shim was generated against
    live_checksum     what the live firm package hashes to right now
    status            ok | drift | probe_failed | shim_missing_checksum
    detail            human-readable note

The log is append-only and bounded (default 500 rows). Useful for
post-mortem ("how long was the contract drifted before someone caught
it?") and for the dashboard's bridge-health card.

API
---
    log_fingerprint(probe_result)         # append once
    read_fingerprint_log(n=100)           # tail N rows newest-first
    last_drift_window()                   # (drift_started_at, drift_ended_at)
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_ROWS = 500


def _state_dir() -> Path:
    if os.name == "nt":
        default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mnq_bot" / "state"
    else:
        default = Path.home() / ".local" / "state" / "mnq_bot"
    return Path(os.environ.get("MNQ_BOT_STATE_DIR", default))


def _log_path() -> Path:
    p = _state_dir() / "shim_fingerprint_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class FingerprintRow:
    ts: str
    locked_checksum: str | None
    live_checksum: str | None
    status: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trim_to_max_rows(path: Path, max_rows: int) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) <= max_rows:
        return
    keep = lines[-max_rows:]
    fd, tmp_name = tempfile.mkstemp(prefix=".fingerprint_log.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def log_fingerprint(
    probe_result: Any,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> FingerprintRow:
    """Append one row from a ``ContractProbeResult``.

    De-duplicates: if the most recent row has the same status + same
    live_checksum, we skip the write — the log only records *changes*
    + the first datapoint. Keeps the file from filling with identical
    rows on every firm_health tick.
    """
    row = FingerprintRow(
        ts=datetime.now(UTC).isoformat(timespec="seconds"),
        locked_checksum=getattr(probe_result, "locked_checksum", None),
        live_checksum=getattr(probe_result, "live_checksum", None),
        status=(
            probe_result.status.value
            if hasattr(probe_result.status, "value")
            else str(probe_result.status)
        ),
        detail=str(getattr(probe_result, "detail", ""))[:240],
    )
    path = _log_path()
    if path.exists():
        with path.open("rb") as f:
            try:
                f.seek(max(0, path.stat().st_size - 4096))
                tail = f.read().decode("utf-8", errors="replace")
            except OSError:
                tail = ""
        last_line = tail.strip().rsplit("\n", 1)[-1] if tail.strip() else ""
        if last_line:
            try:
                last = json.loads(last_line)
                if (
                    last.get("status") == row.status
                    and last.get("live_checksum") == row.live_checksum
                    and last.get("locked_checksum") == row.locked_checksum
                ):
                    return row  # de-dup: identical state, no append
            except json.JSONDecodeError:
                pass
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row.as_dict()) + "\n")
    _trim_to_max_rows(path, max_rows)
    return row


def read_fingerprint_log(n: int | None = None) -> list[FingerprintRow]:
    """Return rows newest-first; cap at ``n`` if given."""
    path = _log_path()
    if not path.exists():
        return []
    rows: list[FingerprintRow] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(
                FingerprintRow(
                    ts=d.get("ts", ""),
                    locked_checksum=d.get("locked_checksum"),
                    live_checksum=d.get("live_checksum"),
                    status=d.get("status", "?"),
                    detail=str(d.get("detail", "")),
                )
            )
    rows.reverse()
    if n is not None:
        rows = rows[:n]
    return rows


def last_drift_window() -> tuple[str | None, str | None]:
    """Return (drift_started_at, drift_ended_at) for the most recent drift
    interval, or (None, None) if no drift has ever been recorded.

    drift_started_at: timestamp of the OLDEST consecutive 'drift' row
    drift_ended_at:   timestamp of the row immediately AFTER drift ended
                      (None if currently drifting)
    """
    rows = read_fingerprint_log()
    rows = list(reversed(rows))  # oldest -> newest for chronological scan
    drift_start: str | None = None
    drift_end: str | None = None
    in_drift = False
    for r in rows:
        if r.status == "drift":
            if not in_drift:
                drift_start = r.ts
                in_drift = True
            drift_end = None  # still drifting
        else:
            if in_drift:
                drift_end = r.ts
                in_drift = False
                # don't break -- we want the MOST RECENT window
                drift_start_candidate = drift_start
                drift_start = drift_start_candidate
    return drift_start, drift_end


__all__ = [
    "DEFAULT_MAX_ROWS",
    "FingerprintRow",
    "last_drift_window",
    "log_fingerprint",
    "read_fingerprint_log",
]
