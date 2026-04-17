"""[REAL] Canonical-form content hash for strategy specs.

The hash is over the YAML-equivalent dump of the spec model with:
- keys sorted at every level
- the existing `content_hash` field zeroed (it's an output, not an input)
- Decimals serialized as their canonical decimal string
- datetimes serialized as ISO-8601 with timezone

This means two semantically identical specs hash identically regardless
of file formatting, key order, or trailing whitespace.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from mnq.spec.schema import StrategySpec


def _normalize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    if isinstance(obj, tuple):
        return [_normalize(v) for v in obj]
    if isinstance(obj, Decimal):
        # canonical decimal: strip exponent, no trailing zeros that change meaning
        return str(obj.normalize()) if obj == obj.to_integral_value() and abs(obj) >= 1 else str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def hash_spec(spec: StrategySpec) -> str:
    """Compute sha256 of the canonical form of the spec.

    Returns a string like 'sha256:9f1bc4...'.
    """
    raw = spec.model_dump(mode="python")
    raw["strategy"]["content_hash"] = ""
    canonical = _normalize(raw)
    payload = json.dumps(canonical, separators=(",", ":"), sort_keys=False, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def stamp_hash(spec: StrategySpec) -> StrategySpec:
    """Return a copy of spec with `strategy.content_hash` set."""
    h = hash_spec(spec)
    data = spec.model_dump()
    data["strategy"]["content_hash"] = h
    return StrategySpec.model_validate(data)
