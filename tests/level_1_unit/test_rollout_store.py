"""[REAL] Unit tests for RolloutStore — JSON persistence for TieredRollout state.

Contract under test:
    * dump(rollout) → plain dict that round-trips through load(dict)
    * Every field, including event log, Decimal, datetime, and enum state,
      survives the round trip byte-identical (by public-observable state)
    * save_all + load_all round-trip the full dict
    * Atomic write: partial .tmp files never leak to the real path
    * load_all on a missing file returns {} (first-run safe)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mnq.risk.rollout_store import (
    SCHEMA_VERSION,
    RolloutStore,
    dump,
    load,
)
from mnq.risk.tiered_rollout import (
    RolloutState,
    TieredRollout,
)


# ---------------------------------------------------------------------------
# Dump / load primitives
# ---------------------------------------------------------------------------
class TestDumpLoadPrimitives:
    def test_dump_is_json_serializable(self) -> None:
        r = TieredRollout.initial("orb")
        d = dump(r)
        # Must survive json.dumps without a custom encoder.
        json.dumps(d)

    def test_roundtrip_fresh_rollout(self) -> None:
        r = TieredRollout.initial("orb")
        r2 = load(dump(r))
        assert r2.variant == r.variant
        assert r2.max_tier == r.max_tier
        assert r2.state is r.state
        assert r2.tier == r.tier
        assert r2.allowed_qty() == r.allowed_qty()

    def test_roundtrip_after_halt(self) -> None:
        r = TieredRollout.initial("orb")
        r.halt(at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC), reason="operator halt")
        r2 = load(dump(r))
        assert r2.state is RolloutState.HALTED
        assert r2.allowed_qty() == 0
        log = r2.event_log()
        assert len(log) == 1
        assert log[0].event_type == "halt"
        assert "operator halt" in log[0].reason

    def test_decimal_fields_preserved(self) -> None:
        r = TieredRollout.initial("orb")
        r.record_trade(Decimal("12.34"), datetime(2026, 4, 1, 10, 0, tzinfo=UTC))
        r.record_trade(Decimal("-5.67"), datetime(2026, 4, 1, 10, 5, tzinfo=UTC))
        r2 = load(dump(r))
        # The counters are internal but must survive — we probe via allowed_qty
        # being unchanged, plus equal state dumps.
        assert dump(r2) == dump(r)

    def test_event_log_preserved_in_order(self) -> None:
        r = TieredRollout.initial("orb")
        r.halt(at=datetime(2026, 4, 1, tzinfo=UTC), reason="first")
        r.resume(at=datetime(2026, 4, 2, tzinfo=UTC), reason="cleared")
        r.halt(at=datetime(2026, 4, 3, tzinfo=UTC), reason="second")
        r2 = load(dump(r))
        log = r2.event_log()
        assert [e.event_type for e in log] == ["halt", "resume", "halt"]
        assert [e.reason for e in log] == [
            "manual: first",
            "cleared",
            "manual: second",
        ]

    def test_custom_config_preserved(self) -> None:
        r = TieredRollout.initial("orb", max_tier=5, min_trades_at_tier=100, min_winning_days=7)
        r2 = load(dump(r))
        assert r2.max_tier == 5
        assert r2.min_trades_at_tier == 100
        assert r2.min_winning_days == 7

    def test_schema_version_present(self) -> None:
        r = TieredRollout.initial("orb")
        d = dump(r)
        assert d["schema_version"] == SCHEMA_VERSION

    def test_load_tolerates_missing_config_keys(self) -> None:
        """Forward compat: a partial config falls back to defaults."""
        partial = {
            "variant": "orb",
            "config": {},  # empty — everything from defaults
            "state": "active",
            "tier": 0,
            "counters": {},
            "event_log": [],
        }
        r = load(partial)
        assert r.variant == "orb"
        assert r.tier == 0
        assert r.state is RolloutState.ACTIVE


# ---------------------------------------------------------------------------
# File-backed store
# ---------------------------------------------------------------------------
class TestRolloutStoreFileIO:
    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        store = RolloutStore(tmp_path / "no_such_file.json")
        assert store.load_all() == {}

    def test_save_then_load_all(self, tmp_path: Path) -> None:
        store = RolloutStore(tmp_path / "rollouts.json")
        a = TieredRollout.initial("orb_only")
        b = TieredRollout.initial("orb_sweep")
        b.halt(at=datetime(2026, 4, 1, tzinfo=UTC), reason="test")
        store.save_all({"orb_only": a, "orb_sweep": b})

        rehydrated = store.load_all()
        assert set(rehydrated.keys()) == {"orb_only", "orb_sweep"}
        assert rehydrated["orb_only"].state is RolloutState.ACTIVE
        assert rehydrated["orb_sweep"].state is RolloutState.HALTED

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "dir" / "rollouts.json"
        store = RolloutStore(nested)
        store.save_all({"orb": TieredRollout.initial("orb")})
        assert nested.exists()
        assert nested.parent.is_dir()

    def test_atomic_write_no_tmp_leak(self, tmp_path: Path) -> None:
        store = RolloutStore(tmp_path / "rollouts.json")
        store.save_all({"orb": TieredRollout.initial("orb")})
        # The .tmp companion should NOT survive a successful write.
        assert not (tmp_path / "rollouts.json.tmp").exists()

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        store = RolloutStore(tmp_path / "rollouts.json")
        a = TieredRollout.initial("orb")
        store.save_all({"orb": a})
        a.halt(at=datetime(2026, 4, 1, tzinfo=UTC), reason="op")
        store.save_all({"orb": a})
        reloaded = store.load_all()["orb"]
        assert reloaded.state is RolloutState.HALTED

    def test_single_variant_save_and_load(self, tmp_path: Path) -> None:
        store = RolloutStore(tmp_path / "rollouts.json")
        assert store.load("orb") is None

        a = TieredRollout.initial("orb")
        store.save(a)
        loaded = store.load("orb")
        assert loaded is not None
        assert loaded.variant == "orb"

    def test_single_variant_save_preserves_others(self, tmp_path: Path) -> None:
        store = RolloutStore(tmp_path / "rollouts.json")
        store.save_all(
            {
                "orb_a": TieredRollout.initial("orb_a"),
                "orb_b": TieredRollout.initial("orb_b"),
            }
        )
        # Update only orb_a.
        a = store.load("orb_a")
        assert a is not None
        a.halt(at=datetime(2026, 4, 1, tzinfo=UTC), reason="only-a")
        store.save(a)

        all_loaded = store.load_all()
        assert set(all_loaded.keys()) == {"orb_a", "orb_b"}
        assert all_loaded["orb_a"].state is RolloutState.HALTED
        assert all_loaded["orb_b"].state is RolloutState.ACTIVE

    def test_variants_returns_sorted(self, tmp_path: Path) -> None:
        store = RolloutStore(tmp_path / "rollouts.json")
        store.save_all(
            {
                "zebra": TieredRollout.initial("zebra"),
                "alpha": TieredRollout.initial("alpha"),
                "mango": TieredRollout.initial("mango"),
            }
        )
        assert store.variants() == ["alpha", "mango", "zebra"]

    def test_round_trip_through_disk(self, tmp_path: Path) -> None:
        """Full fidelity: dump dict equals reloaded dump dict."""
        r = TieredRollout.initial("orb")
        r.halt(at=datetime(2026, 4, 1, tzinfo=UTC), reason="op")
        r.resume(at=datetime(2026, 4, 2, tzinfo=UTC), reason="ok")

        store = RolloutStore(tmp_path / "rollouts.json")
        store.save_all({"orb": r})
        reloaded = store.load_all()["orb"]
        assert dump(r) == dump(reloaded)

    def test_json_is_human_readable(self, tmp_path: Path) -> None:
        store = RolloutStore(tmp_path / "rollouts.json")
        store.save_all({"orb": TieredRollout.initial("orb")})
        text = (tmp_path / "rollouts.json").read_text(encoding="utf-8")
        # Indented output + sorted keys = diffable JSON.
        assert "\n" in text
        assert '"orb"' in text
        # schema_version at top
        parsed = json.loads(text)
        assert parsed["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Corruption / error paths
# ---------------------------------------------------------------------------
class TestErrorPaths:
    def test_corrupt_json_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "rollouts.json"
        p.write_text("{ not valid json", encoding="utf-8")
        store = RolloutStore(p)
        with pytest.raises(json.JSONDecodeError):
            store.load_all()

    def test_wrong_schema_shape_still_loads_empty_variants(self, tmp_path: Path) -> None:
        """If the top-level dict has no 'variants' key, we get {} (not crash)."""
        p = tmp_path / "rollouts.json"
        p.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
        store = RolloutStore(p)
        assert store.load_all() == {}
