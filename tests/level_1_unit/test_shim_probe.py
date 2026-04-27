"""Unit tests for ``src/mnq/_shim_probe.py``.

The probe shells out to ``scripts/firm_bridge.py --probe`` to compute the
live contract checksum, so subprocess-affecting fixtures (sys.modules
monkey-patches, fake firm packages) won't apply. Tests use the real
on-disk bridge + firm package and verify the probe's status branches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnq import _shim_probe as probe


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def real_live_checksum() -> str:
    """The probe's checksum against the real firm package + bridge.
    Skips if the bridge or firm package isn't available."""
    if not probe.BRIDGE_PATH.exists():
        pytest.skip("firm_bridge.py not present in this repo layout")
    try:
        return probe._live_checksum(probe.CONTRACT)
    except ImportError as e:
        pytest.skip(f"firm package not importable: {e}")


def _shim_with_checksum(tmp_path: Path, checksum: str | None) -> Path:
    shim = tmp_path / "firm_runtime.py"
    if checksum is None:
        shim.write_text('"""legacy shim with no checksum line"""\n', encoding="utf-8")
    else:
        shim.write_text(
            f'"""Shim.\n\nGenerated at: 2026-04-25T00:00:00Z\n'
            f'Bridge probe checksum: {checksum}\n"""\n',
            encoding="utf-8",
        )
    return shim


# ---------------------------------------------------------------------------
# verify_shim_contract — happy path
# ---------------------------------------------------------------------------
def test_ok_when_locked_matches_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_live_checksum: str
) -> None:
    monkeypatch.setattr(probe, "SHIM_PATH", _shim_with_checksum(tmp_path, real_live_checksum))
    r = probe.verify_shim_contract()
    assert r.status is probe.ContractStatus.OK
    assert r.ok is True
    assert r.locked_checksum == r.live_checksum == real_live_checksum
    assert r.detail == "contract intact"


# ---------------------------------------------------------------------------
# verify_shim_contract — drift detection
# ---------------------------------------------------------------------------
def test_drift_when_locked_does_not_match_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_live_checksum: str
) -> None:
    monkeypatch.setattr(probe, "SHIM_PATH", _shim_with_checksum(tmp_path, "deadbeef"))
    r = probe.verify_shim_contract()
    assert r.status is probe.ContractStatus.DRIFT
    assert r.ok is False
    assert r.locked_checksum == "deadbeef"
    assert r.live_checksum == real_live_checksum
    assert "contract drift" in r.detail


def test_strict_mode_raises_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_live_checksum: str
) -> None:
    monkeypatch.setattr(probe, "SHIM_PATH", _shim_with_checksum(tmp_path, "0badf00d"))
    with pytest.raises(probe.ShimContractDriftError, match="contract drift"):
        probe.verify_shim_contract(strict=True)


# ---------------------------------------------------------------------------
# verify_shim_contract — error / legacy branches
# ---------------------------------------------------------------------------
def test_returns_legacy_status_when_checksum_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe, "SHIM_PATH", _shim_with_checksum(tmp_path, None))
    r = probe.verify_shim_contract()
    assert r.status is probe.ContractStatus.SHIM_MISSING_CHECKSUM
    assert r.locked_checksum is None
    assert "regenerate" in r.detail


def test_probe_failed_when_bridge_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the on-disk firm_bridge.py vanishes, the probe must fail cleanly
    instead of crashing."""
    monkeypatch.setattr(probe, "SHIM_PATH", _shim_with_checksum(tmp_path, "feedface"))
    monkeypatch.setattr(probe, "BRIDGE_PATH", tmp_path / "no_such_bridge.py")
    r = probe.verify_shim_contract()
    assert r.status is probe.ContractStatus.PROBE_FAILED
    assert r.locked_checksum == "feedface"
    assert r.live_checksum is None
    assert "firm_bridge.py not found" in r.detail


def test_strict_raises_when_bridge_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "SHIM_PATH", _shim_with_checksum(tmp_path, "feedface"))
    monkeypatch.setattr(probe, "BRIDGE_PATH", tmp_path / "no_such_bridge.py")
    with pytest.raises(probe.ShimContractDriftError, match="firm_bridge.py not found"):
        probe.verify_shim_contract(strict=True)


def test_probe_failed_when_shim_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "SHIM_PATH", tmp_path / "nope.py")
    r = probe.verify_shim_contract()
    assert r.status is probe.ContractStatus.PROBE_FAILED
    assert "shim missing" in r.detail


def test_probe_uses_real_shim_when_run_against_live_firm() -> None:
    """End-to-end probe against the actual on-disk shim — should be one of
    the four documented statuses, never crash."""
    r = probe.verify_shim_contract()
    assert r.status in (
        probe.ContractStatus.OK,
        probe.ContractStatus.SHIM_MISSING_CHECKSUM,
        probe.ContractStatus.DRIFT,
        probe.ContractStatus.PROBE_FAILED,
    )
