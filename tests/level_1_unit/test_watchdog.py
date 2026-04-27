"""Tests for scripts/watchdog.py — Phase 0 process watchdog."""

from __future__ import annotations

import json

# Import the script functions directly
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from watchdog import (
    check_heartbeat,
    check_journal,
    check_pid,
    render_report,
    run_checks,
)


class TestCheckHeartbeat:
    def test_no_file_is_ok_bootstrap(self):
        with patch("watchdog.HEARTBEAT_PATH", Path("/nonexistent")):
            ok, msg, ctx = check_heartbeat()
            assert ok  # Bootstrap — no file yet is OK

    def test_fresh_heartbeat_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb.json"
            path.write_text(json.dumps({"ts": datetime.now(tz=UTC).isoformat()}))
            with patch("watchdog.HEARTBEAT_PATH", path):
                ok, msg, ctx = check_heartbeat()
                assert ok
                assert "alive" in msg

    def test_stale_heartbeat_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb.json"
            old = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
            path.write_text(json.dumps({"ts": old}))
            with patch("watchdog.HEARTBEAT_PATH", path):
                ok, msg, ctx = check_heartbeat()
                assert not ok
                assert "stale" in msg


class TestCheckJournal:
    def test_no_journal_is_ok_bootstrap(self):
        with patch("watchdog.JOURNAL_PATH", Path("/nonexistent")):
            ok, msg, ctx = check_journal()
            assert ok  # Bootstrap

    # Note: Full journal tests require a real SQLite file,
    # covered in level_3_parity tests


class TestCheckPid:
    def test_no_pid_file_is_ok(self):
        with patch("watchdog.PID_PATH", Path("/nonexistent")):
            ok, msg, ctx = check_pid()
            assert ok  # No PID = not running, that's fine


class TestRunChecks:
    def test_all_bootstrap_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("watchdog.HEARTBEAT_PATH", Path("/nonexistent")),
                patch("watchdog.JOURNAL_PATH", Path("/nonexistent")),
                patch("watchdog.PID_PATH", Path("/nonexistent")),
                patch("watchdog.WATCHDOG_STATE_PATH", Path(tmp) / "state.json"),
                patch("watchdog.WATCHDOG_ALERT_PATH", Path(tmp) / "alert.json"),
                patch("watchdog.GATE_PATH", Path(tmp) / "gate.json"),
            ):
                result = run_checks()
                assert result["all_ok"]


class TestRenderReport:
    def test_renders_markdown(self):
        result = {
            "ts": "2026-04-16T12:00:00Z",
            "all_ok": True,
            "checks": {
                "heartbeat": {"ok": True, "message": "alive (5s)"},
                "journal": {"ok": True, "message": "healthy"},
                "process": {"ok": True, "message": "running"},
            },
        }
        md = render_report(result)
        assert "ALL OK" in md
        assert "heartbeat" in md
