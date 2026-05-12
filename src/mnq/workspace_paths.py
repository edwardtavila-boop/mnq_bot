"""Canonical workspace path helpers for the MNQ bot package."""

from __future__ import annotations

import os
from pathlib import Path

_CANONICAL_WORKSPACE_NAME = "EvolutionaryTradingAlgo"


def child_repo_root() -> Path:
    """Return the root of the mnq_bot child repository."""
    return Path(__file__).resolve().parents[2]


def resolve_workspace_root(start: Path | None = None) -> Path:
    """Resolve the canonical ETA workspace root without writing outside it."""
    configured = os.environ.get("EVOLUTIONARY_TRADING_ALGO_ROOT") or os.environ.get(
        "ETA_WORKSPACE_ROOT"
    )
    if configured:
        return Path(configured).expanduser().resolve()

    current = Path(start or child_repo_root()).resolve()
    for candidate in (current, *current.parents):
        if candidate.name == _CANONICAL_WORKSPACE_NAME:
            return candidate
    return current


def workspace_mnq_data_root(start: Path | None = None) -> Path:
    """Return the workspace-level MNQ data root used by live/shadow readers."""
    return resolve_workspace_root(start) / "mnq_data"
