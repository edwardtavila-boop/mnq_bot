"""Tests for spec approval manifest."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnq.spec.hash import hash_spec, stamp_hash
from mnq.spec.loader import load_spec
from mnq.spec.manifest import (
    ApprovalManifest,
    UnapprovedSpecError,
    require_approved,
)


@pytest.fixture
def test_manifest_path(tmp_path: Path) -> Path:
    """Create a path for test manifest."""
    return tmp_path / "test_manifest.yaml"


@pytest.fixture
def sample_spec(tmp_path: Path) -> Path:
    """Create a sample spec file for testing."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from mnq.spec.schema import (
        CommissionModel,
        Entry,
        EntrySide,
        Execution,
        Exit,
        InitialStop,
        Instrument,
        PerSessionRisk,
        PerTradeRisk,
        PerWeekRisk,
        PositionRisk,
        PositionSizing,
        Risk,
        Session,
        SessionWindow,
        StrategyMeta,
        StrategySpec,
        TakeProfit,
        Timeframes,
    )

    meta = StrategyMeta(
        id="test_v1",
        semver="1.0.0",
        created_by="test",
        created_at=datetime.now(UTC),
        content_hash="",
    )

    spec = StrategySpec(
        strategy=meta,
        instrument=Instrument(),
        timeframes=Timeframes(),
        session=Session(
            windows=[
                SessionWindow(name="RTH", start="09:30", end="16:00")
            ]
        ),
        features=[],
        entry=Entry(
            long=EntrySide(all_of=["test"]),
            short=EntrySide(all_of=["test"]),
        ),
        position_sizing=PositionSizing(
            mode="fixed_contracts",
            fixed_contracts=1,
        ),
        exit=Exit(
            initial_stop=InitialStop(type="fixed_ticks", ticks=10),
            take_profit=TakeProfit(type="fixed_ticks", value=Decimal("20")),
        ),
        risk=Risk(
            per_trade=PerTradeRisk(max_loss_usd=Decimal("100")),
            per_session=PerSessionRisk(max_loss_usd=Decimal("1000"), max_trades=10),
            per_week=PerWeekRisk(max_loss_usd=Decimal("5000")),
            position=PositionRisk(),
        ),
        execution=Execution(),
        commission_model=CommissionModel(per_contract_per_side_usd=Decimal("1.50")),
    )

    spec = stamp_hash(spec)

    spec_path = tmp_path / "test_spec.yaml"
    from mnq.spec.loader import dump_spec
    dump_spec(spec, spec_path)

    return spec_path


def test_empty_manifest() -> None:
    """Test empty manifest finds nothing."""
    manifest = ApprovalManifest(specs=())
    assert manifest.find("sha256:nonexistent") is None


def test_approve_adds_entry() -> None:
    """Test approve adds a spec entry."""
    manifest = ApprovalManifest(specs=())

    new_manifest = manifest.approve(
        spec_id="v1_baseline",
        content_hash="sha256:abc123",
        approved_by="ed",
        gauntlet_run_id="GR-2026-04-14-001",
        notes="Baseline spec",
    )

    assert len(new_manifest.specs) == 1
    assert new_manifest.specs[0].spec_id == "v1_baseline"
    assert new_manifest.specs[0].content_hash == "sha256:abc123"


def test_find_approved_spec() -> None:
    """Test find returns ApprovedSpec when hash exists."""
    manifest = ApprovalManifest(specs=())

    new_manifest = manifest.approve(
        spec_id="v1",
        content_hash="sha256:abc123",
        approved_by="ed",
        gauntlet_run_id="GR-001",
    )

    found = new_manifest.find("sha256:abc123")
    assert found is not None
    assert found.spec_id == "v1"
    assert found.content_hash == "sha256:abc123"


def test_load_save_round_trip(test_manifest_path: Path) -> None:
    """Test load/save preserves data."""
    manifest = ApprovalManifest(specs=())

    # Approve and save
    manifest = manifest.approve(
        spec_id="v1",
        content_hash="sha256:abc123",
        approved_by="ed",
        gauntlet_run_id="GR-001",
        notes="Test spec",
    )
    manifest.save(test_manifest_path)

    # Load and verify
    loaded = ApprovalManifest.load(test_manifest_path)
    assert len(loaded.specs) == 1
    assert loaded.specs[0].spec_id == "v1"
    assert loaded.specs[0].content_hash == "sha256:abc123"
    assert loaded.specs[0].approved_by == "ed"
    assert loaded.specs[0].gauntlet_run_id == "GR-001"
    assert loaded.specs[0].notes == "Test spec"


def test_require_approved_unknown_hash(sample_spec: Path) -> None:
    """Test require_approved raises when hash not in manifest."""
    spec = load_spec(sample_spec)
    manifest = ApprovalManifest(specs=())

    with pytest.raises(UnapprovedSpecError):
        require_approved(spec, manifest)


def test_require_approved_known_hash(sample_spec: Path) -> None:
    """Test require_approved returns ApprovedSpec when hash is known."""
    spec = load_spec(sample_spec)
    computed_hash = hash_spec(spec)

    manifest = ApprovalManifest(specs=())
    manifest = manifest.approve(
        spec_id=spec.strategy.id,
        content_hash=computed_hash,
        approved_by="ed",
        gauntlet_run_id="GR-001",
    )

    approved = require_approved(spec, manifest)
    assert approved.spec_id == spec.strategy.id
    assert approved.content_hash == computed_hash


def test_require_approved_hash_drift_raises(sample_spec: Path) -> None:
    """Test require_approved raises when stamped != computed hash."""
    spec = load_spec(sample_spec)

    # Modify the spec's content_hash to simulate drift
    spec_data = spec.model_dump()
    spec_data["strategy"]["content_hash"] = "sha256:wronghash"
    spec_drifted = spec.model_validate(spec_data)

    manifest = ApprovalManifest(specs=())
    # Approve the correct hash
    manifest = manifest.approve(
        spec_id=spec.strategy.id,
        content_hash=hash_spec(spec),
        approved_by="ed",
        gauntlet_run_id="GR-001",
    )

    # Should raise UnapprovedSpecError due to drift
    with pytest.raises(UnapprovedSpecError):
        require_approved(spec_drifted, manifest, allow_drift=False)


def test_require_approved_hash_drift_with_allow_drift(sample_spec: Path) -> None:
    """Test require_approved allows drift when allow_drift=True."""
    spec = load_spec(sample_spec)

    # Simulate hash drift
    spec_data = spec.model_dump()
    spec_data["strategy"]["content_hash"] = "sha256:wronghash"
    spec_drifted = spec.model_validate(spec_data)

    manifest = ApprovalManifest(specs=())
    # Approve the correct hash
    manifest = manifest.approve(
        spec_id=spec.strategy.id,
        content_hash=hash_spec(spec),
        approved_by="ed",
        gauntlet_run_id="GR-001",
    )

    # Should not raise when allow_drift=True
    approved = require_approved(spec_drifted, manifest, allow_drift=True)
    assert approved.content_hash == hash_spec(spec)


def test_cli_verify_approved(sample_spec: Path, test_manifest_path: Path) -> None:
    """Test CLI verify command exits 0 for approved spec."""
    from mnq.cli.spec import app

    # Load spec and compute hash
    spec = load_spec(sample_spec)
    computed_hash = hash_spec(spec)

    # Create and save manifest with approval
    manifest = ApprovalManifest(specs=())
    manifest = manifest.approve(
        spec_id=spec.strategy.id,
        content_hash=computed_hash,
        approved_by="test_user",
        gauntlet_run_id="GR-2026-04-14-001",
    )
    manifest.save(test_manifest_path)

    # Run verify command
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["verify", str(sample_spec), "--manifest", str(test_manifest_path)],
    )

    assert result.exit_code == 0


def test_cli_verify_not_approved(sample_spec: Path, test_manifest_path: Path) -> None:
    """Test CLI verify command exits 1 for unapproved spec."""
    from mnq.cli.spec import app

    # Create empty manifest
    manifest = ApprovalManifest(specs=())
    manifest.save(test_manifest_path)

    # Run verify command
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["verify", str(sample_spec), "--manifest", str(test_manifest_path)],
    )

    assert result.exit_code == 1


def test_cli_approve(sample_spec: Path, test_manifest_path: Path) -> None:
    """Test CLI approve command appends to manifest."""
    from mnq.cli.spec import app

    # Run approve command
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "approve",
            str(sample_spec),
            "--manifest", str(test_manifest_path),
            "--approved-by", "test_user",
            "--gauntlet-run-id", "GR-2026-04-14-001",
            "--notes", "Test approval",
        ],
    )

    assert result.exit_code == 0

    # Verify manifest was created and contains the spec
    manifest = ApprovalManifest.load(test_manifest_path)
    assert len(manifest.specs) == 1
    assert manifest.specs[0].approved_by == "test_user"
    assert manifest.specs[0].gauntlet_run_id == "GR-2026-04-14-001"
    assert manifest.specs[0].notes == "Test approval"


def test_cli_approve_idempotent(sample_spec: Path, test_manifest_path: Path) -> None:
    """Test CLI approve is idempotent (doesn't duplicate on second run)."""
    from mnq.cli.spec import app

    runner = CliRunner()

    # First approve
    result1 = runner.invoke(
        app,
        [
            "approve",
            str(sample_spec),
            "--manifest", str(test_manifest_path),
            "--approved-by", "user1",
            "--gauntlet-run-id", "GR-001",
        ],
    )
    assert result1.exit_code == 0

    # Second approve (should detect it's already approved)
    result2 = runner.invoke(
        app,
        [
            "approve",
            str(sample_spec),
            "--manifest", str(test_manifest_path),
            "--approved-by", "user2",
            "--gauntlet-run-id", "GR-002",
        ],
    )
    assert result2.exit_code == 0

    # Verify manifest still has only 1 entry
    manifest = ApprovalManifest.load(test_manifest_path)
    assert len(manifest.specs) == 1
    assert manifest.specs[0].approved_by == "user1"  # Original approver
