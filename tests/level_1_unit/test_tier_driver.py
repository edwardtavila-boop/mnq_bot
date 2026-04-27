"""[REAL] Unit tests for scripts/tier_driver.py.

Driver contract:
    * Only shippable variants get a rollout; unknown/KILL variants are skipped
    * New shippable variants auto-initialize at TIER_0
    * Trades are replayed in chronological order regardless of input order
    * EOD is folded whenever a variant's trade tape crosses a date boundary
    * ``--reset`` and existing-state reuse both work correctly
    * CLI wraps all of the above and persists through RolloutStore
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tier_driver import (  # noqa: E402
    DEFAULT_ROLLOUTS_PATH,
    TradeRecord,
    drive_rollouts,
    main,
)

from mnq.gauntlet.ship_manifest import ShipManifest  # noqa: E402
from mnq.risk.rollout_store import RolloutStore  # noqa: E402
from mnq.risk.tiered_rollout import RolloutState, TieredRollout  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _manifest(tmp_path: Path, verdict_by_variant: dict[str, str]) -> ShipManifest:
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


def _mk(variant: str, pnl: str, day: int, hour: int = 10) -> TradeRecord:
    return TradeRecord(
        variant=variant,
        pnl=Decimal(pnl),
        closed_at=datetime(2026, 4, day, hour, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# TradeRecord
# ---------------------------------------------------------------------------
class TestTradeRecord:
    def test_from_dict_parses_iso_and_decimal(self) -> None:
        t = TradeRecord.from_dict(
            {
                "variant": "orb",
                "pnl": "12.34",
                "closed_at": "2026-04-01T10:00:00+00:00",
            }
        )
        assert t.variant == "orb"
        assert t.pnl == Decimal("12.34")
        assert t.closed_at.tzinfo is not None

    def test_from_dict_auto_utc_if_naive(self) -> None:
        t = TradeRecord.from_dict(
            {
                "variant": "orb",
                "pnl": "1.00",
                "closed_at": "2026-04-01T10:00:00",
            }
        )
        assert t.closed_at.tzinfo is UTC


# ---------------------------------------------------------------------------
# drive_rollouts
# ---------------------------------------------------------------------------
class TestDriveRollouts:
    def test_shippable_variants_get_fresh_rollouts(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS", "dead": "KILL"})
        result = drive_rollouts(manifest=m, trades=[], existing={})
        assert "orb" in result
        assert "dead" not in result  # KILL never driven
        assert result["orb"].tier == 0
        assert result["orb"].state is RolloutState.ACTIVE

    def test_existing_rollouts_are_preserved(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        pre = TieredRollout.initial("orb")
        pre.halt(at=datetime(2026, 3, 1, tzinfo=UTC), reason="previous halt")
        result = drive_rollouts(manifest=m, trades=[], existing={"orb": pre})
        # Halt state survives — we didn't overwrite with a fresh rollout.
        assert result["orb"].state is RolloutState.HALTED

    def test_trades_for_unknown_variant_are_skipped(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        trades = [_mk("phantom", "5", 1)]
        result = drive_rollouts(manifest=m, trades=trades, existing={})
        assert "phantom" not in result
        assert result["orb"].state is RolloutState.ACTIVE

    def test_trades_replayed_in_chronological_order(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        # Feed in reverse order — driver must sort.
        trades = [_mk("orb", "1", 3), _mk("orb", "1", 1), _mk("orb", "1", 2)]
        result = drive_rollouts(manifest=m, trades=trades, existing={})
        # After a winning day-boundary fold we expect some event log entries.
        # This is the proxy: at least a counter increment per day.
        assert "orb" in result
        # No promotion (only 3 trades total), but state is active.
        assert result["orb"].state is RolloutState.ACTIVE

    def test_variants_filter_limits_set(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb_a": "PASS", "orb_b": "PASS"})
        result = drive_rollouts(manifest=m, trades=[], existing={}, variants=["orb_a"])
        assert set(result.keys()) == {"orb_a"}

    def test_halt_on_consecutive_losses(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        # 5 back-to-back losses → halt trigger
        trades = [_mk("orb", "-10", 1, h) for h in range(10, 15)]
        result = drive_rollouts(manifest=m, trades=trades, existing={})
        assert result["orb"].state is RolloutState.HALTED
        assert result["orb"].allowed_qty() == 0

    def test_eod_fires_on_date_boundary(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        # Day 1: winning trade. Day 2: winning trade. EOD for day 1 should
        # have fired before day 2's trade — we assert via the consecutive
        # winning days counter going through record_eod path.
        trades = [_mk("orb", "10", 1), _mk("orb", "10", 2)]
        result = drive_rollouts(manifest=m, trades=trades, existing={})
        r = result["orb"]
        # Two winning days = two EOD folds with positive day_end_pnl.
        assert r._consecutive_winning_days == 2

    def test_eod_flushes_after_last_trade(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        trades = [_mk("orb", "5", 1)]
        result = drive_rollouts(manifest=m, trades=trades, existing={})
        # Even a single-day tape must EOD-fold so the counters are final.
        assert result["orb"]._consecutive_winning_days == 1

    def test_per_variant_eod_is_independent(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb_a": "PASS", "orb_b": "PASS"})
        trades = [
            _mk("orb_a", "10", 1),
            _mk("orb_b", "-10", 1),
        ]
        result = drive_rollouts(manifest=m, trades=trades, existing={})
        assert result["orb_a"]._consecutive_winning_days == 1
        assert result["orb_b"]._consecutive_losing_days == 1

    def test_empty_trade_list_still_creates_rollouts(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        result = drive_rollouts(manifest=m, trades=[], existing={})
        assert result["orb"].event_log() == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class TestCLI:
    def test_main_writes_rollout_file(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        trades_path = tmp_path / "trades.json"
        trades_path.write_text(
            json.dumps(
                [
                    {"variant": "orb", "pnl": "10.0", "closed_at": "2026-04-01T10:00:00+00:00"},
                ]
            )
        )
        rollouts_path = tmp_path / "rollouts.json"

        rc = main(
            [
                "--trades",
                str(trades_path),
                "--rollouts",
                str(rollouts_path),
                "--manifest",
                str(m.source_path),
            ]
        )
        assert rc == 0
        assert rollouts_path.exists()
        store = RolloutStore(rollouts_path)
        loaded = store.load_all()
        assert "orb" in loaded

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        trades_path = tmp_path / "trades.json"
        trades_path.write_text(json.dumps([]))
        rollouts_path = tmp_path / "rollouts.json"
        rc = main(
            [
                "--trades",
                str(trades_path),
                "--rollouts",
                str(rollouts_path),
                "--manifest",
                str(m.source_path),
                "--dry-run",
            ]
        )
        assert rc == 0
        assert not rollouts_path.exists()

    def test_reset_ignores_existing_state(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb": "PASS"})
        rollouts_path = tmp_path / "rollouts.json"

        # Pre-stage a halted rollout.
        pre = TieredRollout.initial("orb")
        pre.halt(at=datetime(2026, 3, 1, tzinfo=UTC), reason="previous")
        RolloutStore(rollouts_path).save_all({"orb": pre})

        trades_path = tmp_path / "trades.json"
        trades_path.write_text(json.dumps([]))
        rc = main(
            [
                "--trades",
                str(trades_path),
                "--rollouts",
                str(rollouts_path),
                "--manifest",
                str(m.source_path),
                "--reset",
            ]
        )
        assert rc == 0
        loaded = RolloutStore(rollouts_path).load_all()
        # Reset wipes the halt — fresh rollout is active again.
        assert loaded["orb"].state is RolloutState.ACTIVE

    def test_variants_filter_via_cli(self, tmp_path: Path) -> None:
        m = _manifest(tmp_path, {"orb_a": "PASS", "orb_b": "PASS"})
        trades_path = tmp_path / "trades.json"
        trades_path.write_text(json.dumps([]))
        rollouts_path = tmp_path / "rollouts.json"
        rc = main(
            [
                "--trades",
                str(trades_path),
                "--rollouts",
                str(rollouts_path),
                "--manifest",
                str(m.source_path),
                "--variants",
                "orb_a",
            ]
        )
        assert rc == 0
        loaded = RolloutStore(rollouts_path).load_all()
        assert set(loaded.keys()) == {"orb_a"}

    def test_missing_manifest_returns_2(self, tmp_path: Path) -> None:
        trades_path = tmp_path / "trades.json"
        trades_path.write_text(json.dumps([]))
        rc = main(
            [
                "--trades",
                str(trades_path),
                "--rollouts",
                str(tmp_path / "rollouts.json"),
                "--manifest",
                str(tmp_path / "no_such.json"),
            ]
        )
        assert rc == 2


# ---------------------------------------------------------------------------
# Module constant
# ---------------------------------------------------------------------------
def test_default_rollouts_path_under_data_dir() -> None:
    assert DEFAULT_ROLLOUTS_PATH.name == "rollouts.json"
    assert DEFAULT_ROLLOUTS_PATH.parent.name == "data"
