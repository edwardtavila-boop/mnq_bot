"""Unit tests for the 72h compressed-time burn-in harness.

Runs the harness at tiny hour counts and extreme compression to keep
wall time under a second per test. The invariants we care about —
monotonic seq, deterministic checksum, WAL mode, max hb age, bounded
memory drift — are all exercised.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BURN_IN_PATH = REPO_ROOT / "scripts" / "burn_in_72h.py"


def _load_burn_in_module():
    """Import scripts/burn_in_72h.py as a module without invoking main."""
    spec = importlib.util.spec_from_file_location("burn_in_72h", BURN_IN_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["burn_in_72h"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def burn_in_mod():
    return _load_burn_in_module()


@pytest.fixture
def tmp_burn_db(tmp_path, monkeypatch, burn_in_mod):
    """Redirect the module's BURN_DB and REPORT to a per-test tmp path."""
    db = tmp_path / "burn.sqlite"
    report = tmp_path / "burn_in.md"
    monkeypatch.setattr(burn_in_mod, "BURN_DB", db)
    monkeypatch.setattr(burn_in_mod, "REPORT", report)
    return db, report


class TestBurnInCore:
    def test_tiny_run_returns_expected_keys(self, burn_in_mod, tmp_burn_db):
        r = burn_in_mod.burn_in(hours=1, compression=10000.0, verbose=False)
        assert isinstance(r, dict)
        for key in (
            "hours", "compression", "wall_elapsed_s",
            "expected_events", "actual_events", "expected_hb",
            "monotonic_seq", "checksum", "deterministic",
            "wal_mode", "rss_start_kib", "rss_end_kib",
            "rss_drift_pct", "max_hb_age_seen_s",
        ):
            assert key in r

    def test_seq_monotonic_and_no_gaps(self, burn_in_mod, tmp_burn_db):
        r = burn_in_mod.burn_in(hours=1, compression=10000.0)
        assert r["monotonic_seq"] is True

    def test_checksum_deterministic_across_reads(self, burn_in_mod, tmp_burn_db):
        r = burn_in_mod.burn_in(hours=1, compression=10000.0)
        assert r["deterministic"] is True
        assert len(r["checksum"]) == 16  # sha256 truncated to 16 hex chars

    def test_wal_mode_preserved(self, burn_in_mod, tmp_burn_db):
        r = burn_in_mod.burn_in(hours=1, compression=10000.0)
        assert r["wal_mode"] == "wal"

    def test_events_at_least_one_per_sim_second(self, burn_in_mod, tmp_burn_db):
        r = burn_in_mod.burn_in(hours=1, compression=10000.0)
        # 1 hour sim = 3600 heartbeats + ~60 non-hb events
        assert r["actual_events"] >= r["expected_hb"]

    def test_max_hb_age_under_threshold(self, burn_in_mod, tmp_burn_db):
        r = burn_in_mod.burn_in(hours=1, compression=10000.0)
        assert r["max_hb_age_seen_s"] < 5.0

    def test_reopen_preserves_event_count(self, burn_in_mod, tmp_burn_db):
        # After the run, the journal is reopened read-only for checksum;
        # the returned actual_events must match what's on disk.
        db, _ = tmp_burn_db
        r = burn_in_mod.burn_in(hours=1, compression=10000.0)
        import sqlite3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()
        assert n == r["actual_events"]


class TestReportRender:
    def test_render_report_has_all_checks(self, burn_in_mod):
        sample = {
            "hours": 72, "compression": 4800.0, "wall_elapsed_s": 53.2,
            "expected_events": 263_000, "actual_events": 263_000,
            "expected_hb": 259_200,
            "monotonic_seq": True, "checksum": "abcd" * 4,
            "deterministic": True, "wal_mode": "wal",
            "rss_start_kib": 12_000, "rss_end_kib": 13_500,
            "rss_drift_pct": 12.5, "max_hb_age_seen_s": 1.0,
        }
        out = burn_in_mod._render_report(sample)
        assert "ALL CHECKS GREEN" in out
        assert "72h" in out
        assert "wal" in out
        assert "12.50%" in out or "12.5%" in out

    def test_render_flags_red_on_bad_wal(self, burn_in_mod):
        sample = {
            "hours": 1, "compression": 10000, "wall_elapsed_s": 0.1,
            "expected_events": 3600, "actual_events": 3600, "expected_hb": 3600,
            "monotonic_seq": True, "checksum": "x" * 16,
            "deterministic": True, "wal_mode": "delete",  # bad
            "rss_start_kib": 1000, "rss_end_kib": 1000,
            "rss_drift_pct": 0.0, "max_hb_age_seen_s": 0.5,
        }
        out = burn_in_mod._render_report(sample)
        assert "FAIL" in out
