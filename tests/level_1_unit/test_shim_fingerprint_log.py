"""Tests for src/mnq/_shim_fingerprint_log.py."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MNQ_BOT_STATE_DIR", str(tmp_path))
    import importlib

    import mnq._shim_fingerprint_log as fl

    importlib.reload(fl)
    return tmp_path


class _FakeStatus:
    def __init__(self, val: str) -> None:
        self.value = val


class _FakeProbeResult:
    def __init__(self, status: str, locked: str | None, live: str | None, detail: str = ""):
        self.status = _FakeStatus(status)
        self.locked_checksum = locked
        self.live_checksum = live
        self.detail = detail


def test_log_then_read_round_trip() -> None:
    from mnq import _shim_fingerprint_log as fl

    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    rows = fl.read_fingerprint_log()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].locked_checksum == "abc"


def test_dedup_skips_duplicate_consecutive_rows() -> None:
    from mnq import _shim_fingerprint_log as fl

    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    assert len(fl.read_fingerprint_log()) == 1


def test_status_change_does_append_a_new_row() -> None:
    from mnq import _shim_fingerprint_log as fl

    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    fl.log_fingerprint(_FakeProbeResult("drift", "abc", "xyz"))
    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    assert len(fl.read_fingerprint_log()) == 3


def test_max_rows_capped() -> None:
    from mnq import _shim_fingerprint_log as fl

    for i in range(20):
        # Vary live_checksum so dedup doesn't kick in
        fl.log_fingerprint(_FakeProbeResult("drift", "abc", f"v{i}"))
    rows = fl.read_fingerprint_log()
    # Each call appends; max_rows default is 500, so all 20 survive
    assert len(rows) == 20

    # Now force a small cap
    for i in range(100):
        fl.log_fingerprint(
            _FakeProbeResult("drift", "abc", f"u{i}"),
            max_rows=50,
        )
    rows = fl.read_fingerprint_log()
    assert len(rows) == 50


def test_last_drift_window_returns_None_when_no_drift() -> None:
    from mnq import _shim_fingerprint_log as fl

    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    start, end = fl.last_drift_window()
    assert start is None and end is None


def test_last_drift_window_currently_drifting_has_no_end() -> None:
    from mnq import _shim_fingerprint_log as fl

    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    fl.log_fingerprint(_FakeProbeResult("drift", "abc", "v1"))
    start, end = fl.last_drift_window()
    assert start is not None
    assert end is None


def test_last_drift_window_resolved_drift_has_both_endpoints() -> None:
    from mnq import _shim_fingerprint_log as fl

    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    fl.log_fingerprint(_FakeProbeResult("drift", "abc", "v1"))
    fl.log_fingerprint(_FakeProbeResult("ok", "abc", "abc"))
    start, end = fl.last_drift_window()
    assert start is not None
    assert end is not None


def test_corrupt_lines_are_skipped() -> None:
    from mnq import _shim_fingerprint_log as fl

    fl._log_path().write_text(
        '{"ts":"x","status":"ok","locked_checksum":"a","live_checksum":"a","detail":""}\n'
        "not-json\n"
        '{"ts":"y","status":"drift","locked_checksum":"a","live_checksum":"b","detail":""}\n',
        encoding="utf-8",
    )
    rows = fl.read_fingerprint_log()
    statuses = [r.status for r in rows]
    assert statuses == ["drift", "ok"]  # newest first


def test_n_clamps_to_requested_count() -> None:
    from mnq import _shim_fingerprint_log as fl

    for i in range(15):
        fl.log_fingerprint(_FakeProbeResult("drift", "abc", f"v{i}"))
    rows = fl.read_fingerprint_log(n=5)
    assert len(rows) == 5
