"""Tests for v0.2.19's drift-summary renderer in
``scripts/run_eta_live.py::_format_drift_summary`` and the
``--inspect`` integration.

Pin the contract:

  * Missing recency_weighted_expectancy_r OR expected_expectancy_r
    -> empty string (no section emitted)
  * |delta| < 0.05R -> EDGE STEADY
  * recency < expected by >= 0.05R -> EDGE FADING
  * recency > expected by >= 0.05R -> EDGE GROWING
  * Output includes E, recency, and delta values
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run_eta_live.py"


@pytest.fixture(scope="module")
def runtime_mod():
    spec = importlib.util.spec_from_file_location(
        "run_eta_live_drift_test", SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eta_live_drift_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# _format_drift_summary
# ---------------------------------------------------------------------------


def test_missing_expected_returns_empty(runtime_mod) -> None:
    """No expected_expectancy_r -> empty string."""
    spec = {"recency_weighted_expectancy_r": 0.5}
    assert runtime_mod._format_drift_summary(spec) == ""


def test_missing_recency_returns_empty(runtime_mod) -> None:
    """No recency -> empty string (caller skips the section)."""
    spec = {"expected_expectancy_r": 0.5}
    assert runtime_mod._format_drift_summary(spec) == ""


def test_none_recency_returns_empty(runtime_mod) -> None:
    """recency_weighted_expectancy_r=None (the v0.2.18 sentinel for
    'no signal') -> empty string."""
    spec = {"expected_expectancy_r": 0.5, "recency_weighted_expectancy_r": None}
    assert runtime_mod._format_drift_summary(spec) == ""


def test_steady_when_delta_small(runtime_mod) -> None:
    """|delta| < 0.05R -> STEADY."""
    spec = {
        "expected_expectancy_r": 0.150,
        "recency_weighted_expectancy_r": 0.155,
    }
    out = runtime_mod._format_drift_summary(spec)
    assert "EDGE STEADY" in out
    assert "+0.150R" in out
    assert "+0.155R" in out
    # delta = +0.005R
    assert "+0.005R" in out


def test_fading_when_recency_below_expected(runtime_mod) -> None:
    """recency < expected by >= 0.05R -> FADING."""
    spec = {
        "expected_expectancy_r": 0.500,
        "recency_weighted_expectancy_r": 0.100,
    }
    out = runtime_mod._format_drift_summary(spec)
    assert "EDGE FADING" in out
    assert "-0.400R" in out


def test_growing_when_recency_above_expected(runtime_mod) -> None:
    """recency > expected by >= 0.05R -> GROWING."""
    spec = {
        "expected_expectancy_r": 0.100,
        "recency_weighted_expectancy_r": 0.500,
    }
    out = runtime_mod._format_drift_summary(spec)
    assert "EDGE GROWING" in out
    assert "+0.400R" in out


def test_threshold_boundary_clearly_growing(runtime_mod) -> None:
    """A delta clearly above 0.05R triggers GROWING (not STEADY).
    Note: delta == 0.05R exactly is at the floating-point boundary;
    tests use delta > 0.05R + epsilon to be deterministic."""
    spec = {
        "expected_expectancy_r": 0.100,
        "recency_weighted_expectancy_r": 0.160,  # delta = +0.060R
    }
    out = runtime_mod._format_drift_summary(spec)
    assert "EDGE GROWING" in out


def test_negative_expected_handled(runtime_mod) -> None:
    """Both expectancies negative still produces a delta correctly."""
    spec = {
        "expected_expectancy_r": -0.300,
        "recency_weighted_expectancy_r": -0.500,
    }
    out = runtime_mod._format_drift_summary(spec)
    # recency more negative than expected -> FADING
    assert "EDGE FADING" in out
    assert "-0.300R" in out
    assert "-0.500R" in out


# ---------------------------------------------------------------------------
# --inspect integration
# ---------------------------------------------------------------------------


def _make_runtime(runtime_mod):
    from mnq.risk.tiered_rollout import TieredRollout

    class _FakeJ:
        def close(self): pass

    class _FakeBook:
        _gate_chain = object()

    class _FakeBreaker:
        def allow_trade(self, *, now=None):
            class _D:
                allowed = True
                reason = "ok"
                detail = ""
            return _D()

    cfg = runtime_mod.RuntimeConfig(
        live=False, max_bars=0, tick_interval_s=0.0,
        variant="r5_real_wide_target",
        state_dir=Path("/tmp/_drift_test"),
        journal_path=Path("/tmp/_drift_test/j.sqlite"),
        skip_promotion_gate=True,
        tape_path=None, firm_review_every=1,
        firm_review_enabled=False,
        inspect=True,
    )
    rollout = TieredRollout.initial(cfg.variant)
    rollout.tier = 1
    return runtime_mod.ApexRuntime(
        cfg=cfg, journal=_FakeJ(), book=_FakeBook(),
        breaker=_FakeBreaker(), rollout=rollout, tape=None,
    )


def test_inspect_emits_drift_section(runtime_mod, capsys) -> None:
    """When the spec_payload has both fields, the drift section
    appears in --inspect output."""
    rt = _make_runtime(runtime_mod)
    spec = {
        "strategy_id": "test",
        "expected_expectancy_r": 0.5,
        "recency_weighted_expectancy_r": 0.1,
    }
    runtime_mod._run_inspect(rt, spec)
    out = capsys.readouterr().out
    assert "drift indicator" in out
    assert "EDGE FADING" in out


def test_inspect_skips_drift_section_when_recency_none(
    runtime_mod, capsys,
) -> None:
    """No recency value -> drift section omitted (no visual noise)."""
    rt = _make_runtime(runtime_mod)
    spec = {
        "strategy_id": "test",
        "expected_expectancy_r": 0.5,
        "recency_weighted_expectancy_r": None,
    }
    runtime_mod._run_inspect(rt, spec)
    out = capsys.readouterr().out
    assert "drift indicator" not in out


def test_drift_appears_before_regime_table(runtime_mod, capsys) -> None:
    """Section ordering: spec -> drift -> regime_table -> bar."""
    rt = _make_runtime(runtime_mod)
    spec = {
        "strategy_id": "test",
        "expected_expectancy_r": 0.1,
        "recency_weighted_expectancy_r": 0.15,
        "regime_expectancy": {
            "low-vol-trend": {
                "n_days": 5.0, "expectancy_r": 0.1,
                "total_pnl": 50.0, "pnl_per_day": 10.0,
            },
        },
    }
    runtime_mod._run_inspect(rt, spec)
    out = capsys.readouterr().out
    idx_drift = out.index("drift indicator")
    idx_regime = out.index("regime_expectancy (sorted")
    assert idx_drift < idx_regime
