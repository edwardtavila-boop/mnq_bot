"""[REAL] Load + validate spec YAML files."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mnq.spec.hash import hash_spec, stamp_hash
from mnq.spec.schema import StrategySpec
from mnq.spec.validator import validate_spec


def load_spec(path: str | Path) -> StrategySpec:
    """Load a strategy spec from YAML, validate it, and verify the content hash.

    Raises:
        FileNotFoundError, ValidationError, SpecValidationError, HashMismatchError.
    """
    path = Path(path)
    with path.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    spec = StrategySpec.model_validate(raw)
    validate_spec(spec)

    expected = hash_spec(spec)
    declared = spec.strategy.content_hash
    if declared and declared != expected:
        raise HashMismatchError(
            f"{path}: declared hash {declared} != computed {expected}. "
            "Did the spec change without rehashing? Run `mnq spec rehash`."
        )
    if not declared:
        # Stamp it; useful for newly-written specs that don't yet have a hash
        spec = stamp_hash(spec)
    return spec


def dump_spec(spec: StrategySpec, path: str | Path) -> None:
    """Stamp the hash, dump to YAML."""
    spec = stamp_hash(spec)
    data = spec.model_dump(mode="json", exclude_none=True)
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))


class HashMismatchError(Exception):
    pass
