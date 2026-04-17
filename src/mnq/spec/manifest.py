"""[IMPL] Approval manifest for gauntlet-tested specs.

Registry of specs approved to run in shadow/live mode. The executor must
refuse to run a spec whose content_hash is not in the manifest.

Manifest YAML format:
```yaml
specs:
  - spec_id: v0_1_baseline
    content_hash: sha256:31013abc...
    approved_at: 2026-04-14T10:00:00Z
    approved_by: ed
    gauntlet_run_id: GR-2026-04-14-001
    notes: "Baseline — 15 gates green, expectancy +$3.20/trade"
```
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from mnq.spec.schema import StrategySpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovedSpec:
    """A spec approved to run."""

    spec_id: str
    content_hash: str
    approved_at: datetime
    approved_by: str
    gauntlet_run_id: str
    notes: str

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ApprovedSpec:
        """Parse from YAML-loaded dict."""
        approved_at_str = data.get("approved_at")
        if isinstance(approved_at_str, str):
            approved_at = datetime.fromisoformat(approved_at_str)
        else:
            raise ValueError(f"approved_at must be ISO string, got {approved_at_str}")

        return cls(
            spec_id=str(data.get("spec_id", "")),
            content_hash=str(data.get("content_hash", "")),
            approved_at=approved_at,
            approved_by=str(data.get("approved_by", "")),
            gauntlet_run_id=str(data.get("gauntlet_run_id", "")),
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize to YAML-compatible dict."""
        return {
            "spec_id": self.spec_id,
            "content_hash": self.content_hash,
            "approved_at": self.approved_at.isoformat(),
            "approved_by": self.approved_by,
            "gauntlet_run_id": self.gauntlet_run_id,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ApprovalManifest:
    """Registry of gauntlet-approved specs."""

    specs: tuple[ApprovedSpec, ...]

    @classmethod
    def load(cls, path: Path) -> ApprovalManifest:
        """Load manifest from YAML file.

        Args:
            path: Path to manifest YAML file.

        Returns:
            ApprovalManifest instance.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If YAML is malformed.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest file not found: {path}")

        with path.open() as f:
            data = yaml.safe_load(f)

        if data is None:
            # Empty file
            return cls(specs=())

        specs_list = data.get("specs", [])
        if not isinstance(specs_list, list):
            raise ValueError("specs field must be a list")

        specs = tuple(ApprovedSpec.from_dict(s) for s in specs_list)
        return cls(specs=specs)

    def save(self, path: Path) -> None:
        """Save manifest to YAML file.

        Args:
            path: Path to write manifest to.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {"specs": [s.to_dict() for s in self.specs]}
        with path.open("w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

    def find(self, content_hash: str) -> ApprovedSpec | None:
        """Find an approved spec by content hash.

        Args:
            content_hash: The content_hash to search for.

        Returns:
            ApprovedSpec if found, None otherwise.
        """
        for spec in self.specs:
            if spec.content_hash == content_hash:
                return spec
        return None

    def approve(
        self,
        *,
        spec_id: str,
        content_hash: str,
        approved_by: str,
        gauntlet_run_id: str,
        notes: str = "",
    ) -> ApprovalManifest:
        """Return a new manifest with the spec appended.

        Args:
            spec_id: ID of the spec (e.g., "v0_1_baseline").
            content_hash: The sha256:... hash of the spec.
            approved_by: Approver name/ticket.
            gauntlet_run_id: Back-reference to the gauntlet run.
            notes: Optional approval notes.

        Returns:
            New ApprovalManifest with the spec added.
        """
        new_spec = ApprovedSpec(
            spec_id=spec_id,
            content_hash=content_hash,
            approved_at=datetime.now(),
            approved_by=approved_by,
            gauntlet_run_id=gauntlet_run_id,
            notes=notes,
        )
        return ApprovalManifest(specs=self.specs + (new_spec,))


class UnapprovedSpecError(Exception):
    """Raised when a spec is not in the approval manifest."""

    pass


def require_approved(
    spec: StrategySpec,
    manifest: ApprovalManifest,
    *,
    allow_drift: bool = False,
) -> ApprovedSpec:
    """Enforce spec provenance: check that spec.strategy.content_hash is approved.

    Args:
        spec: The strategy spec to verify.
        manifest: The approval manifest.
        allow_drift: If True, warn but proceed if stamped hash doesn't match
                     computed hash. If False (default), raise on mismatch.

    Returns:
        The ApprovedSpec entry if the hash is in the manifest.

    Raises:
        UnapprovedSpecError: If the hash is not in the manifest or if
                             allow_drift=False and hashes don't match.
    """
    from mnq.spec.hash import hash_spec

    stamped_hash = spec.strategy.content_hash
    computed_hash = hash_spec(spec)

    # Check for hash drift
    if stamped_hash and stamped_hash != computed_hash:
        if allow_drift:
            logger.warning(
                "Spec hash drift: stamped %s != computed %s. Proceeding with allow_drift=True.",
                stamped_hash,
                computed_hash,
            )
        else:
            raise UnapprovedSpecError(
                f"Spec hash mismatch: stamped {stamped_hash} != computed {computed_hash}. "
                "Did the spec change without rehashing?"
            )

    # Use the computed hash for approval lookup (source of truth)
    approved = manifest.find(computed_hash)
    if approved is None:
        raise UnapprovedSpecError(
            f"Spec hash {computed_hash} is not in the approval manifest. "
            f"Run gauntlet first, then 'mnq spec approve'."
        )

    return approved
