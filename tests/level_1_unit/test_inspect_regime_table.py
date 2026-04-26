"""Tests for v0.2.16's regime-table renderer in
``scripts/run_eta_live.py::_format_regime_table`` and the
``--inspect`` integration.

Pin the contract:

  * Empty regime_expectancy -> empty string (no section emitted)
  * Each regime gets a row with n_days / total_pnl / pnl_per_day /
    expectancy_r columns
  * Rows are sorted by expectancy_r desc (strongest evidence first)
  * --inspect output includes the table when regime_expectancy is
    populated
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run_eta_live.py"


@pytest.fixture(scope="module")
def runtime_mod():
    spec = importlib.util.spec_from_file_location(
        "run_eta_live_for_inspect_table_test", SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eta_live_for_inspect_table_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# _format_regime_table
# ---------------------------------------------------------------------------


def test_empty_regime_expectancy_returns_empty_string(runtime_mod) -> None:
    """No regime evidence -> no section."""
    assert runtime_mod._format_regime_table({}) == ""


def test_single_regime_renders_with_required_columns(runtime_mod) -> None:
    """One regime row contains n_days / total_pnl / pnl_per_day /
    expectancy_r values."""
    table = runtime_mod._format_regime_table({
        "low-vol-trend": {
            "n_days": 12.0, "total_pnl": 240.0,
            "pnl_per_day": 20.0, "expectancy_r": 0.5,
        },
    })
    assert "low-vol-trend" in table
    assert "12" in table  # n_days
    assert "+240.00" in table  # total_pnl
    assert "+20.00" in table  # pnl_per_day
    assert "+0.5000R" in table  # expectancy_r
    # Headers
    assert "regime" in table
    assert "n_days" in table


def test_rows_sorted_by_expectancy_desc(runtime_mod) -> None:
    """Strongest expectancy_r appears first in the table body."""
    table = runtime_mod._format_regime_table({
        "low-vol-range": {"n_days": 10.0, "expectancy_r": 0.05,
                          "total_pnl": 50.0, "pnl_per_day": 5.0},
        "low-vol-trend": {"n_days": 5.0, "expectancy_r": 0.50,
                          "total_pnl": 250.0, "pnl_per_day": 50.0},
        "high-vol-range": {"n_days": 3.0, "expectancy_r": -0.20,
                           "total_pnl": -30.0, "pnl_per_day": -10.0},
    })
    # Strongest -> weakest
    idx_trend = table.index("low-vol-trend")
    idx_range = table.index("low-vol-range")
    idx_high = table.index("high-vol-range")
    assert idx_trend < idx_range < idx_high


def test_negative_expectancy_renders_with_minus(runtime_mod) -> None:
    table = runtime_mod._format_regime_table({
        "high-vol-range": {
            "n_days": 5.0, "expectancy_r": -0.123,
            "total_pnl": -150.0, "pnl_per_day": -30.0,
        },
    })
    assert "-0.1230R" in table
    assert "-150.00" in table


def test_table_has_markdown_separator_row(runtime_mod) -> None:
    """The second line is the markdown alignment row -- catches
    accidental drop of header/separator structure."""
    table = runtime_mod._format_regime_table({
        "low-vol-trend": {"n_days": 5.0, "expectancy_r": 0.1,
                          "total_pnl": 25.0, "pnl_per_day": 5.0},
    })
    lines = table.split("\n")
    # First line: header. Second: alignment ("|---|---:|...|"). Third+: data.
    assert lines[0].startswith("| regime")
    assert lines[1].startswith("|---")
    assert "|---:|" in lines[1]


def test_zero_pnl_renders_correctly(runtime_mod) -> None:
    """Zero-P&L regimes still render (with $+0.00 not $-0.00)."""
    table = runtime_mod._format_regime_table({
        "transition": {"n_days": 4.0, "expectancy_r": 0.0,
                       "total_pnl": 0.0, "pnl_per_day": 0.0},
    })
    assert "transition" in table
    assert "$+0.00" in table


def test_int_n_days_not_float(runtime_mod) -> None:
    """n_days renders as an int (no decimal point in the column)."""
    table = runtime_mod._format_regime_table({
        "low-vol-trend": {"n_days": 12.0, "expectancy_r": 0.1,
                          "total_pnl": 120.0, "pnl_per_day": 10.0},
    })
    # The cell should contain "| 12 |" (with int formatting), not "| 12.0 |"
    assert "| 12 |" in table
    assert "| 12.0 |" not in table


def test_missing_field_uses_zero_default(runtime_mod) -> None:
    """A partial regime entry (missing some stats) doesn't crash --
    missing fields default to 0."""
    table = runtime_mod._format_regime_table({
        "transition": {"n_days": 3.0},  # no expectancy / pnl
    })
    assert "transition" in table
    assert "3" in table  # n_days


# ---------------------------------------------------------------------------
# --inspect integration
# ---------------------------------------------------------------------------


def _make_bar():
    from mnq.core.types import Bar
    return Bar(
        ts=datetime(2024, 6, 1, 14, 0, tzinfo=UTC),
        open=Decimal("21000"),
        high=Decimal("21010"),
        low=Decimal("20990"),
        close=Decimal("21005"),
        volume=500,
        timeframe_sec=300,
    )


def _make_runtime(runtime_mod, *, tape_bars=None, review_enabled: bool = True):
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
        state_dir=Path("/tmp/_inspect_table_test"),
        journal_path=Path("/tmp/_inspect_table_test/j.sqlite"),
        skip_promotion_gate=True,
        tape_path=None, firm_review_every=1,
        firm_review_enabled=review_enabled,
        inspect=True,
    )
    rollout = TieredRollout.initial(cfg.variant)
    rollout.tier = 1
    return runtime_mod.ApexRuntime(
        cfg=cfg, journal=_FakeJ(), book=_FakeBook(),
        breaker=_FakeBreaker(), rollout=rollout,
        tape=iter(tape_bars) if tape_bars else None,
    )


def test_inspect_emits_regime_table_when_payload_has_data(
    runtime_mod, capsys,
) -> None:
    rt = _make_runtime(
        runtime_mod, tape_bars=[_make_bar()], review_enabled=False,
    )
    spec = {
        "strategy_id": "test",
        "regime_expectancy": {
            "low-vol-trend": {
                "n_days": 8.0, "expectancy_r": 0.15,
                "total_pnl": 120.0, "pnl_per_day": 15.0,
            },
        },
    }
    runtime_mod._run_inspect(rt, spec)
    out = capsys.readouterr().out
    assert "regime_expectancy (sorted by expectancy_r desc)" in out
    assert "low-vol-trend" in out
    # Headers
    assert "n_days" in out
    assert "expectancy_r" in out


def test_inspect_skips_regime_section_when_empty(
    runtime_mod, capsys,
) -> None:
    """No regime evidence -> the section header is NOT emitted (avoids
    visual noise)."""
    rt = _make_runtime(
        runtime_mod, tape_bars=[_make_bar()], review_enabled=False,
    )
    spec = {"strategy_id": "test", "regime_expectancy": {}}
    runtime_mod._run_inspect(rt, spec)
    out = capsys.readouterr().out
    assert "regime_expectancy (sorted" not in out


def test_inspect_skips_regime_section_when_field_missing(
    runtime_mod, capsys,
) -> None:
    """spec_payload without the regime_expectancy key (older payloads)
    just drops the section silently."""
    rt = _make_runtime(
        runtime_mod, tape_bars=[_make_bar()], review_enabled=False,
    )
    spec = {"strategy_id": "test"}  # no regime_expectancy at all
    runtime_mod._run_inspect(rt, spec)
    out = capsys.readouterr().out
    assert "regime_expectancy (sorted" not in out


def test_inspect_regime_table_appears_before_bar(
    runtime_mod, capsys,
) -> None:
    """Section ordering: spec -> regime table -> bar -> firm verdict."""
    rt = _make_runtime(
        runtime_mod, tape_bars=[_make_bar()], review_enabled=False,
    )
    spec = {
        "strategy_id": "test",
        "regime_expectancy": {
            "low-vol-trend": {
                "n_days": 5.0, "expectancy_r": 0.1,
                "total_pnl": 50.0, "pnl_per_day": 10.0,
            },
        },
    }
    runtime_mod._run_inspect(rt, spec)
    out = capsys.readouterr().out
    idx_table = out.index("regime_expectancy (sorted")
    idx_bar = out.index("--- bar (most recent")
    assert idx_table < idx_bar
