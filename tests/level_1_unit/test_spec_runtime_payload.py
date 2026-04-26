"""Tests for ``mnq.spec.runtime_payload`` -- v0.2.7 closure.

Pin the contract the operator's locked plan demanded:

  > Replace stub spec payload in ApexRuntime per-bar Firm review with
  > real yaml-loaded variant specs. The runtime should produce a
  > calibrated ``spec_payload`` from variant config + baseline yaml +
  > cached backtest stats, NOT a stub.

Covers:
  * build_spec_payload returns a dict with the documented keys
  * Provenance tag tracks which sources were used
  * Stub fallback when no source is available
  * Real backtest stats produce non-stub sample_size + expectancy
  * Variant-specific entry / stop / target strings
  * dd_kill_switch_r derives from per_session.max_loss_usd / risk_dollars
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnq.spec.runtime_payload import (
    BACKTEST_DAILY_JSON,
    BASELINE_YAML,
    TRADES_PER_DAY_PROXY,
    build_spec_payload,
)

REQUIRED_KEYS = {
    "strategy_id",
    "sample_size",
    "expected_expectancy_r",
    "oos_degradation_pct",
    "entry_logic",
    "stop_logic",
    "target_logic",
    "dd_kill_switch_r",
    "regimes_approved",
    "approved_sessions",
    "provenance",
}


def test_build_spec_payload_has_all_required_keys() -> None:
    """The runtime depends on these keys being present. A future
    refactor that drops one breaks the firm review wiring."""
    payload = build_spec_payload("r5_real_wide_target")
    missing = REQUIRED_KEYS - set(payload.keys())
    assert not missing, f"missing keys: {missing}"


def test_real_variant_picks_up_all_three_sources() -> None:
    """When variant_cfg + baseline_yaml + cached_backtest are all
    present, provenance lists all three. r5_real_wide_target is in
    the cached backtest data file."""
    if not BASELINE_YAML.exists():
        pytest.skip("baseline yaml not present in this checkout")
    if not BACKTEST_DAILY_JSON.exists():
        pytest.skip("cached backtest data not present")
    payload = build_spec_payload("r5_real_wide_target")
    assert "variant_cfg" in payload["provenance"]
    assert "baseline_yaml" in payload["provenance"]
    assert "cached_backtest" in payload["provenance"]
    # Must NOT be tagged stub once we have any source.
    assert "stub" not in payload["provenance"]


def test_real_variant_has_nonzero_sample_size() -> None:
    """Real backtest data should produce sample_size >= TRADES_PER_DAY_PROXY
    (one-day worth of trades minimum)."""
    if not BACKTEST_DAILY_JSON.exists():
        pytest.skip("cached backtest data not present")
    payload = build_spec_payload("r5_real_wide_target")
    assert payload["sample_size"] >= TRADES_PER_DAY_PROXY


def test_unknown_variant_falls_back_to_stub() -> None:
    """Variants not in VARIANTS list should still get a payload, marked
    with provenance=['stub'] (or partial provenance if yaml/cache do
    have a partial signal). Either way, the caller can boot."""
    payload = build_spec_payload("totally_made_up_variant_does_not_exist")
    # No variant_cfg matched -> at minimum no calibration came from it.
    assert "variant_cfg" not in payload["provenance"]
    # All required keys still present.
    for key in REQUIRED_KEYS:
        assert key in payload


def test_entry_stop_target_strings_render_variant_knobs() -> None:
    """The Firm RedTeam stage parses these strings; the variant's
    actual EMA periods + risk_ticks + rr should appear."""
    payload = build_spec_payload("r5_real_wide_target")
    # r5_real_wide_target uses ema_fast=9, ema_slow=21, risk_ticks=40, rr=2.0
    assert "EMA9" in payload["entry_logic"]
    assert "EMA21" in payload["entry_logic"]
    assert "40-tick" in payload["stop_logic"]
    assert "2.0R" in payload["target_logic"]


def test_dd_kill_switch_r_derived_from_yaml(tmp_path: Path, monkeypatch) -> None:
    """When baseline yaml is loaded, dd_kill_switch_r should be
    max_loss_usd / risk_dollars_per_trade.

    For r5_real_wide_target: risk_ticks=40, tick_value=$0.50,
    risk_dollars=$20. Baseline yaml: per_session.max_loss_usd=$250.
    Therefore dd_kill_switch_r = 250 / 20 = 12.5R.
    """
    if not BASELINE_YAML.exists():
        pytest.skip("baseline yaml not present in this checkout")
    payload = build_spec_payload("r5_real_wide_target")
    assert payload["dd_kill_switch_r"] == pytest.approx(12.5, rel=0.05)


def test_oos_degradation_at_least_zero(tmp_path: Path) -> None:
    """OOS degradation is a percentage; should be >= 0. Stub case
    returns the pessimistic 100.0%."""
    payload = build_spec_payload("r5_real_wide_target")
    assert payload["oos_degradation_pct"] >= 0.0


def test_no_backtest_data_yields_pessimistic_oos(monkeypatch, tmp_path: Path) -> None:
    """When no backtest data file exists, the Firm should see oos=100%
    (so RedTeam flags the strategy until validation arrives)."""
    fake_path = tmp_path / "missing.json"
    monkeypatch.setattr(
        "mnq.spec.runtime_payload.BACKTEST_DAILY_JSON", fake_path,
    )
    payload = build_spec_payload("r5_real_wide_target")
    assert payload["oos_degradation_pct"] == 100.0
    assert payload["sample_size"] == 0
    assert "cached_backtest" not in payload["provenance"]


def test_synthetic_backtest_data_drives_sample_size(
    monkeypatch, tmp_path: Path,
) -> None:
    """A hand-crafted backtest file with N positive-PnL days should
    produce sample_size = N * TRADES_PER_DAY_PROXY (when no journal
    rate is available)."""
    fake_path = tmp_path / "fake_daily.json"
    fake_path.write_text(
        json.dumps({
            "r5_real_wide_target": {
                "2026-01-01": 50.0,
                "2026-01-02": -20.0,
                "2026-01-03": 30.0,
                "2026-01-04": 40.0,
                "2026-01-05": 0.0,
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mnq.spec.runtime_payload.BACKTEST_DAILY_JSON", fake_path,
    )
    # v0.2.10: pin journal rate to None so the test exercises the
    # TRADES_PER_DAY_PROXY fallback path. Without this patch the
    # actual live_sim journal might have fills that override the
    # proxy and break the deterministic assertion.
    monkeypatch.setattr(
        "mnq.spec.runtime_payload._journal_trades_per_day",
        lambda: None,
    )
    payload = build_spec_payload("r5_real_wide_target")
    assert payload["sample_size"] == 5 * TRADES_PER_DAY_PROXY
    # expectancy_r calculation: total_pnl=100, n_days=5, sample_size=10,
    # risk_dollars = 40 ticks * $0.50 = $20. expectancy_r = 10/10/20 = 0.5R
    expected = (100.0 / 10) / 20.0
    assert payload["expected_expectancy_r"] == pytest.approx(expected, rel=0.01)
    # Approved regimes: at least one positive-PnL day -> normal_vol_trend
    assert "normal_vol_trend" in payload["regimes_approved"]


def test_provenance_is_stub_only_when_all_sources_missing(
    monkeypatch, tmp_path: Path,
) -> None:
    """If neither variant_cfg, yaml, nor backtest is available, the
    provenance must be exactly ['stub']."""
    monkeypatch.setattr(
        "mnq.spec.runtime_payload.BASELINE_YAML", tmp_path / "no_yaml.yaml",
    )
    monkeypatch.setattr(
        "mnq.spec.runtime_payload.BACKTEST_DAILY_JSON",
        tmp_path / "no_data.json",
    )
    payload = build_spec_payload("does_not_exist_variant")
    assert payload["provenance"] == ["stub"]
