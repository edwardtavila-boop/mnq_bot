"""[REAL] Unit tests for mnq.gauntlet.ship_manifest.

The manifest is the hard gate that turns ``edge_forensics`` output into
a machine-enforceable promotion policy. These tests pin:

* loading + schema validation
* invariant: ``shippable`` flag must agree with the verdict label
* read-only query API (``is_shippable``, ``verdict_for``,
  ``require_shippable``, ``shippable_variants``, ``killed_variants``)
* defensive defaults: unknown variant => not shippable (never accidentally
  clear something we didn't analyze)
* I/O behavior: missing file, corrupted JSON, missing required fields
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnq.gauntlet.ship_manifest import (
    KNOWN_VERDICTS,
    SHIPPABLE_VERDICTS,
    ShipManifest,
    ShipManifestError,
    ShipManifestMissingError,
    ShipManifestSchemaError,
    VariantVerdict,
)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
def _variant_payload(
    *,
    verdict: str = "PASS",
    shippable: bool | None = None,
    n_trades: int = 300,
    sharpe: float = 3.2,
    dsr_100: float = 1.0,
    bootstrap_lo: float = 50.0,
    bootstrap_hi: float = 250.0,
    bootstrap_ci_covers_zero: bool = False,
    cost_sensitivity: dict[str, float] | None = None,
    reasons: list[str] | None = None,
) -> dict:
    if shippable is None:
        shippable = verdict in SHIPPABLE_VERDICTS
    if cost_sensitivity is None:
        cost_sensitivity = {"-1.74": 200.0, "-5.00": 150.0, "-10.00": 100.0}
    if reasons is None:
        reasons = []
    return {
        "variant": "__placeholder__",
        "verdict": verdict,
        "shippable": shippable,
        "n_trades": n_trades,
        "sharpe": sharpe,
        "dsr_100": dsr_100,
        "bootstrap_lo": bootstrap_lo,
        "bootstrap_hi": bootstrap_hi,
        "bootstrap_ci_covers_zero": bootstrap_ci_covers_zero,
        "cost_sensitivity": cost_sensitivity,
        "reasons": reasons,
    }


def _manifest_payload(variants: dict[str, dict]) -> dict:
    return {
        "generated": "2026-04-18T10:00:00+00:00",
        "bootstrap_iters": 10000,
        "n_buckets": 4,
        "cost_scenarios_per_trade": [-1.74, -5.0, -10.0],
        "counts": _counts_from_variants(variants),
        "variants": variants,
    }


def _counts_from_variants(variants: dict[str, dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in variants.values():
        v = d["verdict"]
        out[v] = out.get(v, 0) + 1
    return out


def _write_manifest(tmp_path: Path, variants: dict[str, dict]) -> Path:
    path = tmp_path / "edge_forensics.json"
    path.write_text(json.dumps(_manifest_payload(variants)))
    return path


# ---------------------------------------------------------------------------
# Loading + schema
# ---------------------------------------------------------------------------
class TestLoading:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ShipManifestMissingError):
            ShipManifest.from_path(tmp_path / "nope.json")

    def test_corrupt_json_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not: json,")
        with pytest.raises(ShipManifestSchemaError):
            ShipManifest.from_path(p)

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"variants": {}}))
        with pytest.raises(ShipManifestSchemaError, match="generated"):
            ShipManifest.from_path(p)

    def test_variants_must_be_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(
            json.dumps(
                {
                    "generated": "x",
                    "bootstrap_iters": 1,
                    "n_buckets": 1,
                    "variants": [],  # list, not dict
                }
            )
        )
        with pytest.raises(ShipManifestSchemaError, match="variants must be a dict"):
            ShipManifest.from_path(p)

    def test_unknown_verdict_rejected(self, tmp_path: Path) -> None:
        variants = {"x": _variant_payload(verdict="MAYBE", shippable=False)}
        p = _write_manifest(tmp_path, variants)
        with pytest.raises(ShipManifestSchemaError, match="unknown verdict"):
            ShipManifest.from_path(p)

    def test_shippable_flag_must_agree_with_verdict(self, tmp_path: Path) -> None:
        # KILL with shippable=True is a schema violation — never ship a KILL.
        variants = {"x": _variant_payload(verdict="KILL", shippable=True)}
        p = _write_manifest(tmp_path, variants)
        with pytest.raises(ShipManifestSchemaError, match="disagrees"):
            ShipManifest.from_path(p)

    def test_loads_minimal_valid_manifest(self, tmp_path: Path) -> None:
        variants = {"orb_only_pm30": _variant_payload(verdict="PASS")}
        p = _write_manifest(tmp_path, variants)
        m = ShipManifest.from_path(p)
        assert len(m) == 1
        assert "orb_only_pm30" in m


# ---------------------------------------------------------------------------
# is_shippable / verdict_for / require_shippable
# ---------------------------------------------------------------------------
class TestQueries:
    @pytest.fixture
    def manifest(self, tmp_path: Path) -> ShipManifest:
        variants = {
            "orb_only_pm30": _variant_payload(verdict="PASS"),
            "orb_regime_pm30": _variant_payload(verdict="WATCH"),
            "noisy_edge": _variant_payload(verdict="FRAGILE", shippable=False),
            "cost_sunk": _variant_payload(verdict="FAIL", shippable=False),
            "r0_real_baseline": _variant_payload(verdict="KILL", shippable=False),
        }
        p = _write_manifest(tmp_path, variants)
        return ShipManifest.from_path(p)

    def test_pass_is_shippable(self, manifest: ShipManifest) -> None:
        assert manifest.is_shippable("orb_only_pm30") is True

    def test_watch_is_shippable(self, manifest: ShipManifest) -> None:
        assert manifest.is_shippable("orb_regime_pm30") is True

    def test_fragile_is_not_shippable(self, manifest: ShipManifest) -> None:
        assert manifest.is_shippable("noisy_edge") is False

    def test_fail_is_not_shippable(self, manifest: ShipManifest) -> None:
        assert manifest.is_shippable("cost_sunk") is False

    def test_kill_is_not_shippable(self, manifest: ShipManifest) -> None:
        assert manifest.is_shippable("r0_real_baseline") is False

    def test_unknown_variant_is_not_shippable(self, manifest: ShipManifest) -> None:
        """Safe default — unknown = not cleared."""
        assert manifest.is_shippable("never_heard_of_it") is False

    def test_verdict_for_known(self, manifest: ShipManifest) -> None:
        assert manifest.verdict_for("orb_only_pm30") == "PASS"
        assert manifest.verdict_for("r0_real_baseline") == "KILL"

    def test_verdict_for_unknown_returns_UNKNOWN(self, manifest: ShipManifest) -> None:
        assert manifest.verdict_for("mystery") == "UNKNOWN"

    def test_require_shippable_pass_returns_entry(self, manifest: ShipManifest) -> None:
        entry = manifest.require_shippable("orb_only_pm30")
        assert entry.variant == "orb_only_pm30"
        assert entry.verdict == "PASS"

    def test_require_shippable_kill_raises(self, manifest: ShipManifest) -> None:
        with pytest.raises(ShipManifestError, match="not cleared"):
            manifest.require_shippable("r0_real_baseline")

    def test_require_shippable_unknown_raises(self, manifest: ShipManifest) -> None:
        with pytest.raises(ShipManifestError, match="not in the ship manifest"):
            manifest.require_shippable("ghost")


# ---------------------------------------------------------------------------
# Bulk views
# ---------------------------------------------------------------------------
class TestBulkViews:
    @pytest.fixture
    def manifest(self, tmp_path: Path) -> ShipManifest:
        variants = {
            "b_watch": _variant_payload(verdict="WATCH"),
            "a_pass": _variant_payload(verdict="PASS"),
            "z_kill": _variant_payload(verdict="KILL", shippable=False),
            "y_kill": _variant_payload(verdict="KILL", shippable=False),
            "fragile": _variant_payload(verdict="FRAGILE", shippable=False),
        }
        p = _write_manifest(tmp_path, variants)
        return ShipManifest.from_path(p)

    def test_shippable_variants_returns_sorted_pass_watch(self, manifest: ShipManifest) -> None:
        names = manifest.shippable_variants()
        assert names == ["a_pass", "b_watch"]

    def test_killed_variants_returns_sorted_kill_only(self, manifest: ShipManifest) -> None:
        names = manifest.killed_variants()
        assert names == ["y_kill", "z_kill"]

    def test_len_reflects_total_variants(self, manifest: ShipManifest) -> None:
        assert len(manifest) == 5

    def test_contains_operator(self, manifest: ShipManifest) -> None:
        assert "a_pass" in manifest
        assert "ghost" not in manifest
        assert 42 not in manifest  # type: ignore[operator]


# ---------------------------------------------------------------------------
# VariantVerdict dataclass semantics
# ---------------------------------------------------------------------------
class TestVariantVerdict:
    def test_from_dict_preserves_fields(self) -> None:
        d = _variant_payload(verdict="PASS", n_trades=42, sharpe=2.1, dsr_100=0.97)
        v = VariantVerdict.from_dict("foo", d)
        assert v.variant == "foo"
        assert v.verdict == "PASS"
        assert v.shippable is True
        assert v.n_trades == 42
        assert v.sharpe == pytest.approx(2.1)
        assert v.dsr_100 == pytest.approx(0.97)

    def test_from_dict_coerces_reasons_to_tuple(self) -> None:
        d = _variant_payload(verdict="KILL", shippable=False, reasons=["x", "y"])
        v = VariantVerdict.from_dict("foo", d)
        assert v.reasons == ("x", "y")

    def test_from_dict_cost_sensitivity_must_be_dict(self) -> None:
        d = _variant_payload()
        d["cost_sensitivity"] = ["not", "a", "dict"]
        with pytest.raises(ShipManifestSchemaError, match="cost_sensitivity"):
            VariantVerdict.from_dict("foo", d)

    def test_shippable_verdicts_constant_is_correct(self) -> None:
        assert {"PASS", "WATCH"} == SHIPPABLE_VERDICTS

    def test_known_verdicts_covers_all(self) -> None:
        assert {"PASS", "WATCH", "FRAGILE", "FAIL", "KILL"} == KNOWN_VERDICTS


# ---------------------------------------------------------------------------
# Integration with the real manifest (if it exists)
# ---------------------------------------------------------------------------
class TestDefaultManifest:
    """Optional — only runs if the repo's actual manifest is present."""

    def test_default_manifest_loads_if_present(self) -> None:
        from mnq.gauntlet.ship_manifest import DEFAULT_MANIFEST_PATH

        if not DEFAULT_MANIFEST_PATH.exists():
            pytest.skip(f"no manifest at {DEFAULT_MANIFEST_PATH}")
        m = ShipManifest.from_default_path()
        # At least one variant must be parsed
        assert len(m) > 0
        # Every variant's shippable flag must agree with its verdict
        for name, entry in m.variants.items():
            assert entry.shippable == (entry.verdict in SHIPPABLE_VERDICTS), (
                f"{name}: shippable={entry.shippable} vs verdict={entry.verdict}"
            )
