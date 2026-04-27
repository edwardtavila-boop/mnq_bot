"""[REAL] Unit tests for scripts/promotion_pipeline.py.

Pipeline contract:
    * Ship manifest is the 1st gate (and always runs)
    * Journal-health gate reports correctly on missing/corrupt/clean
    * Rollout-state gate blocks HALTED variants
    * A variant passes iff every gate passes
    * Render markdown + JSON artifacts from gate outcomes
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from promotion_pipeline import (  # noqa: E402
    GateResult,
    VariantReport,
    check_journal_health,
    check_rollout_not_halted,
    check_ship_manifest,
    render_markdown,
    run_pipeline,
    write_artifacts,
)

from mnq.gauntlet.ship_manifest import ShipManifest  # noqa: E402
from mnq.risk.rollout_store import RolloutStore  # noqa: E402
from mnq.risk.tiered_rollout import TieredRollout  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _manifest(tmp_path: Path, verdict_by_variant: dict[str, str]) -> ShipManifest:
    """Build a synthetic ship manifest."""
    variants = {}
    counts: dict[str, int] = {}
    for name, verdict in verdict_by_variant.items():
        shippable = verdict in ("PASS", "WATCH")
        variants[name] = {
            "variant": name,
            "verdict": verdict,
            "shippable": shippable,
            "n_trades": 100,
            "sharpe": 2.5,
            "dsr_100": 0.97 if shippable else 0.05,
            "bootstrap_lo": 10.0,
            "bootstrap_hi": 100.0,
            "bootstrap_ci_covers_zero": not shippable,
            "cost_sensitivity": {"-1.74": 50.0, "-5.00": 20.0, "-10.00": -10.0},
            "reasons": [] if shippable else ["edge failed"],
        }
        counts[verdict] = counts.get(verdict, 0) + 1

    payload = {
        "generated": "2026-04-18T00:00:00+00:00",
        "bootstrap_iters": 1000,
        "n_buckets": 4,
        "cost_scenarios_per_trade": [-1.74, -5.0, -10.0],
        "counts": counts,
        "variants": variants,
    }
    p = tmp_path / "edge_forensics.json"
    p.write_text(json.dumps(payload))
    return ShipManifest.from_path(p)


def _make_journal(path: Path, *, n_events: int, gap: bool = False) -> Path:
    """Write a tiny synthetic journal DB matching the schema the checker reads."""
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY,
            event_type TEXT,
            payload TEXT
        )
    """)
    for i in range(1, n_events + 1):
        # If gap requested, skip seq=3 so we have a missing row.
        if gap and i == 3:
            continue
        cur.execute(
            "INSERT INTO events (seq, event_type, payload) VALUES (?, ?, ?)",
            (i, "heartbeat", "{}"),
        )
    con.commit()
    con.close()
    return path


# ---------------------------------------------------------------------------
# Ship-manifest gate
# ---------------------------------------------------------------------------
class TestShipManifestGate:
    def test_pass_verdict_clears(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        assert check_ship_manifest(m, "orb").passed is True

    def test_kill_verdict_blocks(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"baseline": "KILL"})
        g = check_ship_manifest(m, "baseline")
        assert g.passed is False
        assert "KILL" in g.detail or "not cleared" in g.detail

    def test_unknown_variant_blocks(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        g = check_ship_manifest(m, "phantom")
        assert g.passed is False


# ---------------------------------------------------------------------------
# Journal-health gate
# ---------------------------------------------------------------------------
class TestJournalHealthGate:
    def test_missing_journal_fails(self, tmp_path: Path) -> None:
        g = check_journal_health(tmp_path / "nope.sqlite")
        assert g.passed is False
        assert "not found" in g.detail

    def test_clean_journal_passes(self, tmp_path: Path) -> None:
        path = _make_journal(tmp_path / "j.sqlite", n_events=10)
        g = check_journal_health(path)
        assert g.passed is True
        assert "10 rows" in g.detail

    def test_empty_journal_passes(self, tmp_path: Path) -> None:
        """Empty journal is OK — no trades yet, but no corruption."""
        path = _make_journal(tmp_path / "j.sqlite", n_events=0)
        g = check_journal_health(path)
        assert g.passed is True

    def test_gap_in_seq_fails(self, tmp_path: Path) -> None:
        path = _make_journal(tmp_path / "j.sqlite", n_events=10, gap=True)
        g = check_journal_health(path)
        assert g.passed is False
        assert "gap" in g.detail


# ---------------------------------------------------------------------------
# Rollout-state gate
# ---------------------------------------------------------------------------
class TestRolloutGate:
    def test_active_rollout_passes(self) -> None:
        r = TieredRollout.initial("orb")
        g = check_rollout_not_halted(r)
        assert g.passed is True

    def test_halted_rollout_blocks(self) -> None:
        r = TieredRollout.initial("orb")
        r.halt(at=datetime(2026, 4, 1, tzinfo=UTC), reason="operator halt")
        g = check_rollout_not_halted(r)
        assert g.passed is False
        assert "HALTED" in g.detail


# ---------------------------------------------------------------------------
# End-to-end pipeline
# ---------------------------------------------------------------------------
class TestRunPipeline:
    def test_end_to_end_passes_one_variant(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS", "dead": "KILL"})
        j = _make_journal(tmp_path / "j.sqlite", n_events=5)
        reports, metadata = run_pipeline(
            variants=["orb"],
            manifest_path=m.source_path,
            journal_paths=[j],
        )
        assert reports["orb"].cleared_to_promote is True
        assert metadata["n_variants_evaluated"] == 1

    def test_end_to_end_blocks_killed_variant(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"dead": "KILL"})
        j = _make_journal(tmp_path / "j.sqlite", n_events=5)
        reports, _ = run_pipeline(
            variants=["dead"],
            manifest_path=m.source_path,
            journal_paths=[j],
        )
        assert reports["dead"].cleared_to_promote is False
        first_fail = next(g for g in reports["dead"].gates if not g.passed)
        assert first_fail.name == "ship_manifest"

    def test_default_variant_list_is_shippable_set(self, tmp_path: Path) -> None:
        """Omitting variants runs every shippable variant."""
        m = _manifest(tmp_path, {"orb_a": "PASS", "orb_b": "WATCH", "dead": "KILL"})
        j = _make_journal(tmp_path / "j.sqlite", n_events=5)
        reports, _ = run_pipeline(
            manifest_path=m.source_path,
            journal_paths=[j],
        )
        # Only PASS + WATCH make it into the report
        assert set(reports.keys()) == {"orb_a", "orb_b"}

    def test_halted_rollout_overrides_pass_verdict(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        j = _make_journal(tmp_path / "j.sqlite", n_events=5)
        r = TieredRollout.initial("orb")
        r.halt(at=datetime(2026, 4, 1, tzinfo=UTC), reason="maintenance")
        reports, _ = run_pipeline(
            variants=["orb"],
            manifest_path=m.source_path,
            journal_paths=[j],
            rollouts={"orb": r},
        )
        assert reports["orb"].cleared_to_promote is False
        assert reports["orb"].rollout_state == "halted"

    def test_journal_missing_blocks_all_variants(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        reports, _ = run_pipeline(
            variants=["orb"],
            manifest_path=m.source_path,
            journal_paths=[tmp_path / "nonexistent.sqlite"],
        )
        assert reports["orb"].cleared_to_promote is False

    def test_no_journal_configured_is_allowed_paper_mode(self, tmp_path: Path) -> None:
        """Paper dry-run — zero journal paths = skip journal gate."""
        m = _manifest(tmp_path, {"orb": "PASS"})
        reports, _ = run_pipeline(
            variants=["orb"],
            manifest_path=m.source_path,
            journal_paths=[],
        )
        assert reports["orb"].cleared_to_promote is True

    def test_pipeline_reads_rollout_store_by_default(self, tmp_path: Path) -> None:
        """If a rollout store is on disk, run_pipeline picks up its state."""
        m = _manifest(tmp_path, {"orb": "PASS"})
        j = _make_journal(tmp_path / "j.sqlite", n_events=5)

        # Stage a halted rollout in a store file.
        store_path = tmp_path / "rollouts.json"
        halted = TieredRollout.initial("orb")
        halted.halt(at=datetime(2026, 4, 1, tzinfo=UTC), reason="pre-existing halt")
        RolloutStore(store_path).save_all({"orb": halted})

        reports, _ = run_pipeline(
            variants=["orb"],
            manifest_path=m.source_path,
            journal_paths=[j],
            rollout_store_path=store_path,
        )
        assert reports["orb"].cleared_to_promote is False
        assert reports["orb"].rollout_state == "halted"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
class TestRendering:
    def test_markdown_has_variant_and_verdict(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        j = _make_journal(tmp_path / "j.sqlite", n_events=5)
        reports, metadata = run_pipeline(
            variants=["orb"],
            manifest_path=m.source_path,
            journal_paths=[j],
        )
        md = render_markdown(reports, metadata)
        assert "orb" in md
        assert "YES" in md  # cleared
        assert "ship_manifest" in md

    def test_write_artifacts_creates_both_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Redirect REPORT_MD / REPORT_JSON into tmp_path.
        import promotion_pipeline as pp

        monkeypatch.setattr(pp, "REPORT_MD", tmp_path / "out.md")
        monkeypatch.setattr(pp, "REPORT_JSON", tmp_path / "out.json")

        reports = {
            "orb": VariantReport(
                variant="orb",
                cleared_to_promote=True,
                tier=1,
                rollout_state="active",
                gates=[GateResult("ship_manifest", True, "PASS")],
            )
        }
        metadata = {
            "generated": "2026-04-18T00:00:00+00:00",
            "manifest_source": "x",
            "manifest_generated": "y",
            "journal_paths": [],
            "journals_all_passed": True,
            "n_variants_evaluated": 1,
        }
        md_path, json_path = write_artifacts(reports, metadata)
        assert md_path.exists()
        assert json_path.exists()
        payload = json.loads(json_path.read_text())
        assert "variants" in payload
        assert "orb" in payload["variants"]
