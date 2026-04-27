"""B2 deepening (Red Team review 2026-04-25): canonical path registry.

The Red Team review found that 17 scripts had hardcoded paths to a
Linux-sandbox prefix (``/sessions/kind-keen-faraday/...``). On Windows
those paths get interpreted as ``C:\\sessions\\kind-keen-faraday\\...``,
a different location than what the production code expects, which led
to silent journal-file mismatches between writers and readers.

This module replaces the per-script literals with a single source of
truth. Every operationally-meaningful path is:

  1. Resolved against ``REPO_ROOT`` by default (portable -- works on
     Windows, Linux, macOS, and across operator machines).
  2. Overridable via a per-path environment variable so the operator
     can point at a FUSE-mounted scratch volume / sandbox / external
     drive without editing code.
  3. Documented inline so a grep for "MNQ_" env-var names surfaces
     every override knob in one place.

Naming convention: ``<TYPE>_<SUBJECT>`` where TYPE is JOURNAL / DIR /
CSV / CACHE. The env override is the same name uppercased and
prefixed with ``MNQ_`` (e.g. ``LIVE_SIM_JOURNAL`` ->
``MNQ_LIVE_SIM_JOURNAL``).

Usage
-----
    from mnq.core.paths import LIVE_SIM_JOURNAL

    journal = EventJournal(LIVE_SIM_JOURNAL)

The env override is read AT IMPORT TIME -- changing the env var after
import has no effect. This is deliberate: a runtime-mutable path
constant would let a long-running process see two different journal
files in the same session.

Cross-repo coupling
-------------------
Aligned with ``eta_engine/core`` path conventions but does NOT
import from there (per the two-project no-consolidation rule).
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root: ``src/mnq/core/paths.py`` -> ``src/mnq/core`` -> ``src/mnq``
# -> ``src`` -> ``<repo>``. parents[3] gives the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]


def _env_path(env_var: str, default: Path) -> Path:
    """Return ``Path(os.environ[env_var])`` if set, else ``default``."""
    val = os.environ.get(env_var, "").strip()
    return Path(val) if val else default


# ---------------------------------------------------------------------------
# Live-sim journal (the canonical event-sourced journal that
# mnq.risk.gate_chain reads from)
# ---------------------------------------------------------------------------

DATA_ROOT: Path = _env_path("MNQ_DATA_ROOT", REPO_ROOT / "data")
"""Operator-overridable root for ALL data directories.

Useful when the operator runs from a FUSE-mounted scratch volume
(e.g. when SQLite WAL pragmas error on the workspace mount). Set
``MNQ_DATA_ROOT=/path/to/scratch`` and every other path below will
re-route accordingly.
"""

LIVE_SIM_DIR: Path = _env_path("MNQ_LIVE_SIM_DIR", DATA_ROOT / "live_sim")
"""Directory holding the event-sourced live-sim journal.

The directory the live-sim writer creates AND the gate chain reads
from. ``mnq.risk.gate_chain.JOURNAL_PATH`` resolves to
``LIVE_SIM_DIR / "journal.sqlite"`` -- writer and reader stay aligned
by construction.
"""

LIVE_SIM_JOURNAL: Path = _env_path(
    "MNQ_LIVE_SIM_JOURNAL",
    LIVE_SIM_DIR / "journal.sqlite",
)
"""The canonical journal file path. Operationally synonymous with
``mnq.risk.gate_chain.JOURNAL_PATH`` -- both must point at the same
file or the daily-trade-cap, loss-streak, and daily-loss gates will
read a different journal than the writer produces (B2 finding from
the 2026-04-25 Red Team review).
"""


# ---------------------------------------------------------------------------
# Market-data bars (Databento + CSV)
# ---------------------------------------------------------------------------

BARS_DIR: Path = _env_path("MNQ_BARS_DIR", DATA_ROOT / "bars")
"""Root for market-data bar files (Databento parquet + CSV slices)."""

BARS_DATABENTO_DIR: Path = _env_path(
    "MNQ_BARS_DATABENTO_DIR",
    BARS_DIR / "databento",
)
"""Databento parquet directory consumed by volume_profile +
cumulative_delta scripts.
"""

MNQ_1M_CSV: Path = _env_path(
    "MNQ_1M_CSV",
    BARS_DIR / "mnq_1m.csv",
)
"""1-minute MNQ CSV data file (legacy)."""

MNQ_5M_CSV: Path = _env_path(
    "MNQ_5M_CSV",
    BARS_DIR / "mnq_5m.csv",
)
"""5-minute MNQ CSV data file (legacy)."""


# ---------------------------------------------------------------------------
# Parquet cache (read-mostly, can live external to repo)
# ---------------------------------------------------------------------------

PARQUET_CACHE_DIR: Path = _env_path(
    "MNQ_PARQUET_CACHE_DIR",
    DATA_ROOT / ".cache" / "parquet",
)
"""Cache directory for parquet bars. Operator typically points this
at a per-user volume so the cache survives repo wipes."""


# ---------------------------------------------------------------------------
# Reports + state (write-heavy, must stay repo-local)
# ---------------------------------------------------------------------------

REPORTS_DIR: Path = _env_path("MNQ_REPORTS_DIR", REPO_ROOT / "reports")
"""Markdown analysis output for live-sim runs + post-mortems."""

STATE_DIR: Path = _env_path("MNQ_STATE_DIR", REPO_ROOT / "state")
"""Operator-local state (kill-switch latch, retention manifests, etc.)."""


# ---------------------------------------------------------------------------
# Legacy fallback list (DEPRECATED; kept for transition)
# ---------------------------------------------------------------------------

LEGACY_SANDBOX_PREFIX = "/sessions/kind-keen-faraday"
"""The exact prefix the Red Team review flagged. Modules that
historically appeared in ``_CANDIDATE_JOURNALS`` lists used this as
the FIRST candidate; everything else used REPO_ROOT-relative as the
SECOND. The two-list pattern is preserved (some scripts can run from
either a sandbox or a host machine), but new code should consume
``LIVE_SIM_JOURNAL`` directly and let the env override handle the
sandbox case.
"""


__all__ = [
    "BARS_DATABENTO_DIR",
    "BARS_DIR",
    "DATA_ROOT",
    "LEGACY_SANDBOX_PREFIX",
    "LIVE_SIM_DIR",
    "LIVE_SIM_JOURNAL",
    "MNQ_1M_CSV",
    "MNQ_5M_CSV",
    "PARQUET_CACHE_DIR",
    "REPO_ROOT",
    "REPORTS_DIR",
    "STATE_DIR",
]
