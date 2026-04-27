"""Tests for the v0.2.13 disk-backed regime classification cache.

Pin the contract:

  * Disk cache is round-trippable
  * Stale cache (tape signature mismatch) is invalidated
  * Missing cache file -> graceful fall-through to in-memory rebuild
  * Persist failures are silent (perf optimization, not correctness)
  * Tape signature uses size + mtime (changes invalidate the cache)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnq.spec import runtime_payload
from mnq.spec.runtime_payload import (
    _per_day_regime_map,
    _persist_disk_cache,
    _tape_signature,
    _try_load_disk_cache,
)


@pytest.fixture(autouse=True)
def _clear_in_memory_cache():
    """Each test starts with a clean in-memory cache."""
    runtime_payload._CLASSIFY_CACHE.clear()
    yield
    runtime_payload._CLASSIFY_CACHE.clear()


# ---------------------------------------------------------------------------
# Disk cache I/O
# ---------------------------------------------------------------------------


def test_persist_then_load_round_trips(monkeypatch, tmp_path: Path) -> None:
    """Persist a fake map; load it back; values must match."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(
        runtime_payload,
        "_disk_cache_path",
        lambda: cache_file,
    )
    # Pin tape signature so it doesn't depend on the real disk
    monkeypatch.setattr(
        runtime_payload,
        "_tape_signature",
        lambda: (12345, 67890),
    )
    fake = {"2026-01-01": "low-vol-trend", "2026-01-02": "high-vol-range"}
    _persist_disk_cache(fake)
    assert cache_file.exists()
    loaded = _try_load_disk_cache()
    assert loaded == fake


def test_missing_file_returns_none(monkeypatch, tmp_path: Path) -> None:
    cache_file = tmp_path / "missing.json"
    monkeypatch.setattr(
        runtime_payload,
        "_disk_cache_path",
        lambda: cache_file,
    )
    assert _try_load_disk_cache() is None


def test_stale_cache_returns_none(monkeypatch, tmp_path: Path) -> None:
    """If the tape signature changes after persist, load returns None."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(
        runtime_payload,
        "_disk_cache_path",
        lambda: cache_file,
    )
    # Persist with one signature
    monkeypatch.setattr(
        runtime_payload,
        "_tape_signature",
        lambda: (100, 200),
    )
    _persist_disk_cache({"2026-01-01": "low-vol-trend"})
    # Then load with a different signature
    monkeypatch.setattr(
        runtime_payload,
        "_tape_signature",
        lambda: (101, 200),
    )
    assert _try_load_disk_cache() is None


def test_corrupted_cache_returns_none(monkeypatch, tmp_path: Path) -> None:
    """Malformed JSON in the cache file is ignored (not crashed)."""
    cache_file = tmp_path / "cache.json"
    cache_file.write_text("not valid json at all", encoding="utf-8")
    monkeypatch.setattr(
        runtime_payload,
        "_disk_cache_path",
        lambda: cache_file,
    )
    monkeypatch.setattr(
        runtime_payload,
        "_tape_signature",
        lambda: (1, 2),
    )
    assert _try_load_disk_cache() is None


def test_non_dict_payload_returns_none(monkeypatch, tmp_path: Path) -> None:
    """If the JSON parses but isn't a dict, treat as missing."""
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    monkeypatch.setattr(
        runtime_payload,
        "_disk_cache_path",
        lambda: cache_file,
    )
    monkeypatch.setattr(
        runtime_payload,
        "_tape_signature",
        lambda: (1, 2),
    )
    assert _try_load_disk_cache() is None


def test_persist_failure_is_silent(monkeypatch, tmp_path: Path) -> None:
    """If we can't write the cache file, the function returns silently
    -- no exception. The persist is a perf optimization, not
    correctness."""
    # Use a path that can't be written (file-as-directory)
    blocker = tmp_path / "blocker"
    blocker.write_text("file, not a dir")
    cache_file = blocker / "cache.json"  # parent is a file, mkdir will fail
    monkeypatch.setattr(
        runtime_payload,
        "_disk_cache_path",
        lambda: cache_file,
    )
    monkeypatch.setattr(
        runtime_payload,
        "_tape_signature",
        lambda: (1, 2),
    )
    _persist_disk_cache({"2026-01-01": "low-vol-trend"})  # must not raise


def test_no_tape_signature_skips_persist(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """If the tape isn't on disk, _tape_signature returns None and
    we don't write a cache (sig=None means we can't validate later)."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(
        runtime_payload,
        "_disk_cache_path",
        lambda: cache_file,
    )
    monkeypatch.setattr(
        runtime_payload,
        "_tape_signature",
        lambda: None,
    )
    _persist_disk_cache({"2026-01-01": "low-vol-trend"})
    assert not cache_file.exists()


# ---------------------------------------------------------------------------
# _tape_signature
# ---------------------------------------------------------------------------


def test_tape_signature_changes_on_mtime_bump(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Touching the tape file changes mtime -> signature changes."""
    fake_tape = tmp_path / "tape.csv"
    fake_tape.write_text("time,open,high,low,close,volume\n", encoding="utf-8")
    monkeypatch.setattr(
        "mnq.tape.databento_tape.DEFAULT_DATABENTO_5M",
        fake_tape,
    )
    sig1 = _tape_signature()
    # Change mtime
    import os
    import time

    time.sleep(0.01)  # ensure mtime increases
    os.utime(fake_tape, (time.time() + 5, time.time() + 5))
    sig2 = _tape_signature()
    assert sig1 != sig2


def test_tape_signature_missing_returns_none(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mnq.tape.databento_tape.DEFAULT_DATABENTO_5M",
        tmp_path / "does_not_exist.csv",
    )
    assert _tape_signature() is None


# ---------------------------------------------------------------------------
# _per_day_regime_map cache resolution order
# ---------------------------------------------------------------------------


def test_per_day_uses_in_memory_first(monkeypatch) -> None:
    """In-memory cache hits avoid touching disk + tape."""
    runtime_payload._CLASSIFY_CACHE["default"] = {
        "2026-01-01": "low-vol-trend",
    }
    # Sabotage disk + tape: should not be called
    monkeypatch.setattr(
        runtime_payload,
        "_try_load_disk_cache",
        lambda: pytest.fail("disk cache called when in-memory is hot"),
    )
    result = _per_day_regime_map()
    assert result == {"2026-01-01": "low-vol-trend"}


def test_per_day_uses_disk_when_memory_cold(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Cold in-memory + warm disk: should not re-classify the tape."""
    monkeypatch.setattr(
        runtime_payload,
        "_try_load_disk_cache",
        lambda: {"2026-01-01": "high-vol-range"},
    )

    # If the tape is touched, the test should fail
    def _fake_tape_load(*a, **kw):
        pytest.fail("tape was loaded when disk cache was warm")

    monkeypatch.setattr(
        "mnq.tape.iter_databento_bars",
        _fake_tape_load,
    )
    result = _per_day_regime_map()
    assert result == {"2026-01-01": "high-vol-range"}


# ---------------------------------------------------------------------------
# Disk cache shape
# ---------------------------------------------------------------------------


def test_persisted_cache_includes_signature_and_n_days(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The cache file format must include tape_signature + n_days
    so an operator can grep the cache age + size without parsing it."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(
        runtime_payload,
        "_disk_cache_path",
        lambda: cache_file,
    )
    monkeypatch.setattr(
        runtime_payload,
        "_tape_signature",
        lambda: (12345, 67890),
    )
    fake = {f"2026-01-{i:02d}": "low-vol-trend" for i in range(1, 16)}
    _persist_disk_cache(fake)
    data = json.loads(cache_file.read_text())
    assert "tape_signature" in data
    assert "n_days" in data
    assert "per_day" in data
    assert data["n_days"] == 15
