"""
Frozen-tree gate for ``eta_v3_framework/v1_locked/``.

v1 is intentionally locked (see ``eta_v3_framework/v1_locked/LOCKED.txt``).
Reproducibility experiments and out-of-sample backtests reference v1 as
the constant baseline against which v2/v3 are evaluated. A silent edit
to v1 invalidates every comparison built on top of it.

This test asserts:

  1. every file present at lock time is still present and content-identical
  2. no NEW file has been added under v1_locked/ (because that would
     change the import surface even if the existing files are untouched)

Override path: if v1 must legitimately change (e.g., the lock is being
refreshed to a new baseline), regenerate ``.frozen_hashes.json`` with the
command embedded in its `_regen_command` field, then commit both. Don't
modify v1 files without updating the manifest in the same change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

LOCKED_DIR = Path(__file__).resolve().parents[2] / "eta_v3_framework" / "v1_locked"
HASH_FILE = LOCKED_DIR / ".frozen_hashes.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest() -> dict[str, str]:
    if not HASH_FILE.exists():
        pytest.skip(f".frozen_hashes.json missing at {HASH_FILE}")
    raw = json.loads(HASH_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)}


def test_v1_locked_dir_exists() -> None:
    assert LOCKED_DIR.is_dir(), f"v1_locked directory missing at {LOCKED_DIR}"


def test_v1_locked_locked_marker_present() -> None:
    marker = LOCKED_DIR / "LOCKED.txt"
    assert marker.exists(), (
        f"LOCKED.txt missing at {marker}. v1 lock marker has been removed; "
        "either restore the marker or remove the .frozen_hashes.json manifest "
        "if v1 is being un-frozen intentionally."
    )


def test_no_new_files_added_under_v1_locked() -> None:
    expected = set(_load_manifest())
    actual = {p.name for p in LOCKED_DIR.iterdir() if p.is_file()}
    actual.discard(".frozen_hashes.json")  # the manifest itself is allowed
    extras = actual - expected
    assert not extras, (
        f"new file(s) added under v1_locked: {sorted(extras)}. "
        "v1 is frozen for reproducibility; either delete the new file(s) or "
        "intentionally refresh the freeze (regenerate .frozen_hashes.json)."
    )


@pytest.mark.parametrize(
    "name,expected_hash", sorted(_load_manifest().items()) if HASH_FILE.exists() else []
)
def test_v1_locked_file_unchanged(name: str, expected_hash: str) -> None:
    path = LOCKED_DIR / name
    assert path.exists(), (
        f"v1_locked file missing: {name}. Frozen baseline disturbed; "
        "restore the file from VCS or intentionally refresh the freeze."
    )
    actual = _sha256(path)
    assert actual == expected_hash, (
        f"v1_locked file modified: {name}\n"
        f"  expected: {expected_hash}\n"
        f"  actual:   {actual}\n"
        "v1 is frozen for reproducibility (see LOCKED.txt). A silent edit "
        "invalidates every walk-forward comparison built on top of v1.\n"
        "If the change is intentional, regenerate .frozen_hashes.json using "
        "the command embedded in its `_regen_command` field, then commit "
        "BOTH the file edit and the manifest update in the same change."
    )
