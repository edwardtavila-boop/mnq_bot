"""Tests for v0.2.17's ``_check_regime_evidence`` doctor check.

Pin the contract:

  * Returns ok    when at least one variant is KEEP
  * Returns warn  when no KEEP but at least one WATCH
  * Returns warn  when ALL are PRUNE (NOT fail -- prevents the
    cached-backtest emptiness from blocking paper operations)
  * Status string never "fail" so the run_eta_live --live boot
    path doesn't trip on it
  * Errors during classification surface as warn, not crash
"""
from __future__ import annotations

import pytest

from mnq.cli.doctor import _check_regime_evidence

# ---------------------------------------------------------------------------
# Real-state smoke
# ---------------------------------------------------------------------------


def test_check_runs_against_real_variants() -> None:
    """The check must run end-to-end against the actual
    strategy_v2.VARIANTS without raising."""
    result = _check_regime_evidence()
    assert result.name == "regime_evidence"
    assert result.status in ("ok", "warn", "fail")
    assert "variants=" in result.detail


def test_status_is_never_fail() -> None:
    """Doctor's `_check_doctor` boot guard fails on any 'fail'.
    regime_evidence MUST stay at warn-or-better so it doesn't block
    live boot. (This is intentional: lack of cached-backtest edge
    is a signal, not a structural block.)"""
    result = _check_regime_evidence()
    assert result.status != "fail"


def test_summary_reports_keep_watch_prune_counts() -> None:
    """The detail string must surface the bucket counts so the
    operator can grep / parse them."""
    result = _check_regime_evidence()
    detail = result.detail
    assert "KEEP=" in detail
    assert "WATCH=" in detail
    assert "PRUNE=" in detail


# ---------------------------------------------------------------------------
# Status mapping (with mocked classifier)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_pruner(monkeypatch):
    """Inject a fake variant_pruner module into sys.modules so the
    doctor check uses our synthesized rows."""
    import importlib.util
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    real_path = repo_root / "scripts" / "variant_pruner.py"
    spec = importlib.util.spec_from_file_location(
        "_doctor_variant_pruner", real_path,
    )
    real_pruner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real_pruner)

    def _make(rows):
        class _Stub:
            KEEP = real_pruner.KEEP
            WATCH = real_pruner.WATCH
            PRUNE = real_pruner.PRUNE

            @staticmethod
            def _build_classified():
                return rows
        return _Stub

    yield _make


def _patched_check(monkeypatch, stub_module) -> object:
    """Patch importlib.util.spec_from_file_location to return a
    spec that loads our stub instead of variant_pruner.py."""
    class _FakeLoader:
        def exec_module(self, module):
            module.KEEP = stub_module.KEEP
            module.WATCH = stub_module.WATCH
            module.PRUNE = stub_module.PRUNE
            module._build_classified = stub_module._build_classified

    class _FakeSpec:
        loader = _FakeLoader()

    def _fake_spec_from_file(name, loc):
        return _FakeSpec()

    def _fake_module_from_spec(spec):
        import types
        return types.ModuleType("_stubbed_pruner")

    monkeypatch.setattr(
        "importlib.util.spec_from_file_location", _fake_spec_from_file,
    )
    monkeypatch.setattr(
        "importlib.util.module_from_spec", _fake_module_from_spec,
    )
    return _check_regime_evidence()


def test_keep_variant_yields_ok(fake_pruner, monkeypatch) -> None:
    rows = [
        {"variant": "v1", "bucket": "KEEP",
         "reason": "edge", "provenance": ["cached_backtest"],
         "n_total": 30, "expected_expectancy_r": 0.5},
        {"variant": "v2", "bucket": "PRUNE",
         "reason": "no edge", "provenance": ["stub"],
         "n_total": 0, "expected_expectancy_r": 0.0},
    ]
    stub = fake_pruner(rows)
    result = _patched_check(monkeypatch, stub)
    assert result.status == "ok"
    assert "KEEP=1" in result.detail
    assert "PRUNE=1" in result.detail


def test_only_watch_variants_yields_warn(fake_pruner, monkeypatch) -> None:
    rows = [
        {"variant": "v1", "bucket": "WATCH",
         "reason": "thin", "provenance": ["cached_backtest"],
         "n_total": 30, "expected_expectancy_r": 0.5},
        {"variant": "v2", "bucket": "PRUNE",
         "reason": "no edge", "provenance": ["stub"],
         "n_total": 0, "expected_expectancy_r": 0.0},
    ]
    stub = fake_pruner(rows)
    result = _patched_check(monkeypatch, stub)
    assert result.status == "warn"
    assert "WATCH=1" in result.detail


def test_all_prune_yields_warn_not_fail(fake_pruner, monkeypatch) -> None:
    """Even when 100% of variants are PRUNE, status stays warn so the
    live boot guard doesn't trip. The reason: cached-backtest emptiness
    is a calibration signal, not a structural block on operation."""
    rows = [
        {"variant": "v1", "bucket": "PRUNE",
         "reason": "no edge", "provenance": ["stub"],
         "n_total": 0, "expected_expectancy_r": 0.0},
        {"variant": "v2", "bucket": "PRUNE",
         "reason": "no edge", "provenance": ["stub"],
         "n_total": 0, "expected_expectancy_r": 0.0},
    ]
    stub = fake_pruner(rows)
    result = _patched_check(monkeypatch, stub)
    assert result.status == "warn"
    assert "PRUNE=2" in result.detail
    # Helpful pointer for the operator
    assert "variant_pruner" in result.detail


def test_no_variants_yields_warn(fake_pruner, monkeypatch) -> None:
    """Empty VARIANTS list (e.g. operator pruned them all) -> warn.
    Doctor doesn't refuse to report; just signals the pipeline is
    empty."""
    stub = fake_pruner([])
    result = _patched_check(monkeypatch, stub)
    assert result.status == "warn"
    assert "no variants" in result.detail.lower()


def test_classifier_exception_yields_warn(monkeypatch) -> None:
    """If the classifier raises, the check returns warn (not crash)."""

    class _RaisingLoader:
        def exec_module(self, module):
            raise RuntimeError("simulated classifier crash")

    class _RaisingSpec:
        loader = _RaisingLoader()

    monkeypatch.setattr(
        "importlib.util.spec_from_file_location",
        lambda name, loc: _RaisingSpec(),
    )
    monkeypatch.setattr(
        "importlib.util.module_from_spec",
        lambda spec: __import__("types").ModuleType("_stub"),
    )
    result = _check_regime_evidence()
    assert result.status == "warn"
    # Clear pointer to what failed
    assert (
        "classifier" in result.detail
        or "RuntimeError" in result.detail
        or "not loadable" in result.detail
    )


# ---------------------------------------------------------------------------
# run_all_checks integration
# ---------------------------------------------------------------------------


def test_regime_evidence_appears_in_run_all_checks() -> None:
    """Adding a check requires wiring it into run_all_checks. This
    test catches future drift if someone removes it from the list."""
    from mnq.cli.doctor import run_all_checks
    results = run_all_checks(strict=False)
    names = {r.name for r in results}
    assert "regime_evidence" in names
