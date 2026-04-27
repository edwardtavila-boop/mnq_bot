"""Tests for the v0.2.11 ``--inspect`` diagnostic mode in
``scripts/run_eta_live.py``.

Pin the contract:

  * --inspect prints the full spec_payload as JSON
  * --inspect prints the most recent tape bar (when tape is configured)
  * --inspect surfaces the Firm verdict (when firm_review enabled)
  * --inspect does NOT enter the tick loop (no TickStats updates)
  * --inspect respects --no-firm-review (skips the verdict section)
  * --inspect handles "no tape" gracefully (prints "bar: none")
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run_eta_live.py"


@pytest.fixture(scope="module")
def runtime_mod():
    spec = importlib.util.spec_from_file_location(
        "run_eta_live_for_inspect_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eta_live_for_inspect_test"] = module
    spec.loader.exec_module(module)
    return module


def _make_bar(runtime_mod):
    """Construct one fixture Bar."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from mnq.core.types import Bar

    return Bar(
        ts=datetime(2024, 6, 1, 14, 0, tzinfo=UTC),
        open=Decimal("21000.00"),
        high=Decimal("21010.00"),
        low=Decimal("20990.00"),
        close=Decimal("21005.00"),
        volume=500,
        timeframe_sec=300,
    )


def _make_runtime(
    runtime_mod,
    *,
    inspect: bool = True,
    review_enabled: bool = True,
    tape_bars=None,
    rollout_tier: int = 1,
):
    """Construct an ApexRuntime suitable for inspect-mode testing."""
    from mnq.risk.tiered_rollout import TieredRollout

    class _FakeJournal:
        def close(self):
            pass

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
        live=False,
        max_bars=0,
        tick_interval_s=0.0,
        variant="r5_real_wide_target",
        state_dir=Path("/tmp/_inspect_test"),
        journal_path=Path("/tmp/_inspect_test/j.sqlite"),
        skip_promotion_gate=True,
        tape_path=None,
        firm_review_every=1,
        firm_review_enabled=review_enabled,
        inspect=inspect,
    )
    rollout = TieredRollout.initial(cfg.variant)
    rollout.tier = rollout_tier
    tape = iter(tape_bars) if tape_bars else None
    return runtime_mod.ApexRuntime(
        cfg=cfg,
        journal=_FakeJournal(),
        book=_FakeBook(),
        breaker=_FakeBreaker(),
        rollout=rollout,
        tape=tape,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_inspect_prints_spec_payload(runtime_mod, capsys) -> None:
    """The first section of inspect output is the spec_payload JSON."""
    rt = _make_runtime(runtime_mod, tape_bars=[_make_bar(runtime_mod)])
    spec = {
        "strategy_id": "r5_real_wide_target",
        "sample_size": 30,
        "expected_expectancy_r": 0.05,
        "oos_degradation_pct": 50.0,
        "provenance": ["variant_cfg", "baseline_yaml", "cached_backtest"],
    }
    rc = runtime_mod._run_inspect(rt, spec)
    assert rc == runtime_mod.EX_OK
    out = capsys.readouterr().out
    assert "--- spec_payload (full) ---" in out
    assert '"strategy_id": "r5_real_wide_target"' in out
    assert '"sample_size": 30' in out


def test_inspect_prints_bar_section_when_tape_present(
    runtime_mod,
    capsys,
) -> None:
    """With a tape bar, the second section dumps the bar's OHLCV."""
    rt = _make_runtime(runtime_mod, tape_bars=[_make_bar(runtime_mod)])
    rc = runtime_mod._run_inspect(rt, {})
    out = capsys.readouterr().out
    assert "--- bar (most recent tape entry) ---" in out
    assert '"open": 21000.0' in out
    assert '"close": 21005.0' in out
    assert rc == runtime_mod.EX_OK


def test_inspect_no_tape_prints_none_section(
    runtime_mod,
    capsys,
) -> None:
    """Without a tape, the bar section says 'none' and exits cleanly."""
    rt = _make_runtime(runtime_mod, tape_bars=None)
    rc = runtime_mod._run_inspect(rt, {})
    out = capsys.readouterr().out
    assert "--- bar: none" in out
    assert rc == runtime_mod.EX_OK


def test_inspect_no_firm_review_skips_verdict(
    runtime_mod,
    capsys,
) -> None:
    """With --no-firm-review, the verdict section is replaced with a
    'DISABLED' line."""
    rt = _make_runtime(
        runtime_mod,
        review_enabled=False,
        tape_bars=[_make_bar(runtime_mod)],
    )
    runtime_mod._run_inspect(rt, {})
    out = capsys.readouterr().out
    assert "firm review: DISABLED" in out


def test_inspect_with_firm_review_prints_verdict(
    runtime_mod,
    monkeypatch,
    capsys,
) -> None:
    """When firm_review is enabled and the shim is available, the
    inspect output includes the PM verdict JSON."""

    def _fake_review(**kwargs):
        return {
            "pm": {
                "verdict": "APPROVE",
                "probability": 0.78,
                "reasoning": "in-scope spec, healthy rate",
                "primary_driver": "expectancy",
            },
        }

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review",
        _fake_review,
        raising=True,
    )
    rt = _make_runtime(
        runtime_mod,
        review_enabled=True,
        tape_bars=[_make_bar(runtime_mod)],
    )
    runtime_mod._run_inspect(rt, {})
    out = capsys.readouterr().out
    assert "--- firm verdict (PM stage) ---" in out
    assert '"verdict": "APPROVE"' in out
    assert '"pm_probability": 0.78' in out


def test_inspect_does_not_update_tickstats(
    runtime_mod,
    monkeypatch,
) -> None:
    """Inspect mode is read-only diagnostic. It must NOT increment
    bars_processed, orders_submitted, etc."""

    def _fake_review(**kwargs):
        return {
            "pm": {"verdict": "APPROVE", "probability": 0.5},
        }

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review",
        _fake_review,
        raising=True,
    )
    rt = _make_runtime(runtime_mod, tape_bars=[_make_bar(runtime_mod)])
    runtime_mod._run_inspect(rt, {})
    # TickStats should be all zeros -- inspect didn't touch them
    assert rt.stats.bars_processed == 0
    assert rt.stats.orders_submitted == 0
    assert rt.stats.firm_reviews_run == 0


def test_inspect_handles_shim_import_error(
    runtime_mod,
    monkeypatch,
    capsys,
) -> None:
    """If the firm shim isn't importable, inspect prints a graceful
    message and exits 0 (matches the runtime's fail-open behavior)."""
    import builtins

    real_import = builtins.__import__

    def _raise_for_firm_runtime(name, *args, **kwargs):
        if name == "mnq.firm_runtime":
            raise ImportError("simulated missing firm package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raise_for_firm_runtime)
    rt = _make_runtime(runtime_mod, tape_bars=[_make_bar(runtime_mod)])
    rc = runtime_mod._run_inspect(rt, {})
    assert rc == runtime_mod.EX_OK
    out = capsys.readouterr().out
    assert "shim unavailable" in out


def test_inspect_outputs_valid_json_for_spec(runtime_mod, capsys) -> None:
    """The spec_payload section must produce parseable JSON. If a
    future field has a non-serializable type, this test catches it."""
    rt = _make_runtime(runtime_mod, tape_bars=None)
    spec = {
        "strategy_id": "test",
        "nested": {"a": 1, "b": [1, 2, 3]},
        "provenance": ["stub"],
    }
    runtime_mod._run_inspect(rt, spec)
    out = capsys.readouterr().out
    # Find the spec_payload block and round-trip it
    start = out.find("--- spec_payload (full) ---")
    assert start >= 0
    # JSON starts after the section header line
    after_header = out[start:].split("\n", 1)[1]
    # JSON ends at the next "---" section divider
    end = after_header.find("\n---")
    json_text = after_header[:end] if end >= 0 else after_header
    parsed = json.loads(json_text)
    assert parsed["strategy_id"] == "test"
    assert parsed["nested"]["b"] == [1, 2, 3]
