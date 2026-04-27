"""Runtime contract probe for the Firm bridge shim.

Companion to ``_shim_guard``. Where ``_shim_guard`` verifies the file is
syntactically valid (catching OneDrive truncation), this module verifies
the **contract** the shim was generated against still matches the live
``firm`` package. Drift here is silent: tests stay green because they
mock the agents, but live runs would surface the wrong AgentOutput shape
mid-trade.

The bridge writes a checksum into the shim docstring at generation time
(``Bridge probe checksum: <hex>``). On startup, ``verify_shim_contract``
re-computes the same checksum from the live package and refuses to
return until they match (or, in advisory mode, returns a warning).

Usage::

    from mnq._shim_probe import verify_shim_contract
    result = verify_shim_contract(strict=True)  # raises on drift
    # or:
    result = verify_shim_contract(strict=False)  # returns ContractStatus

The strict path is appropriate for ``live_sim`` startup; the advisory
path is appropriate for read-only operator tools (status pages,
dashboards) that should still render even when the bridge is broken.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = [
    "ContractStatus",
    "ContractProbeResult",
    "ShimContractDriftError",
    "verify_shim_contract",
]

SHIM_PATH = Path(__file__).resolve().parent / "firm_runtime.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = REPO_ROOT / "scripts" / "firm_bridge.py"

# Same contract the bridge writes — see scripts/firm_bridge.py CONTRACT.
CONTRACT: dict[str, tuple[str, ...]] = {
    "firm.types": ("Verdict", "Quadrant"),
    "firm.agents.base": ("Agent", "AgentInput", "AgentOutput"),
    "firm.agents.core": (
        "QuantAgent",
        "RedTeamAgent",
        "RiskManagerAgent",
        "MacroAgent",
        "MicrostructureAgent",
        "PMAgent",
    ),
}

_CHECKSUM_PATTERN = re.compile(r"Bridge probe checksum:\s*([0-9a-fA-F]{8,64})")


class ContractStatus(str, Enum):
    OK = "ok"  # checksums match
    SHIM_MISSING_CHECKSUM = "shim_missing_checksum"  # legacy shim, no probe possible
    DRIFT = "drift"  # checksums differ — contract changed
    PROBE_FAILED = "probe_failed"  # firm package not importable


@dataclass(frozen=True)
class ContractProbeResult:
    status: ContractStatus
    locked_checksum: str | None
    live_checksum: str | None
    detail: str

    @property
    def ok(self) -> bool:
        return self.status is ContractStatus.OK


class ShimContractDriftError(RuntimeError):
    """Raised in strict mode when the live contract diverges from the
    locked one. Includes a hint to rerun ``firm_bridge --integrate``."""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _extract_locked_checksum(shim_text: str) -> str | None:
    m = _CHECKSUM_PATTERN.search(shim_text)
    return m.group(1) if m else None


def _live_checksum(contract: dict[str, Iterable[str]]) -> str:
    """Re-derive the contract fingerprint by shelling out to firm_bridge.py
    --probe and reading the same report shape the bridge serializes when it
    writes the shim. The bridge writes ``reports/firm_integration.json``;
    we apply ``sha256(json.dumps(report, sort_keys=True))[:16]`` to match
    the algorithm in ``write_runtime_shim``.

    Subprocess (rather than direct import) avoids the `from __future__`
    eval-order issues that bite when loading the bridge as a dynamic module.
    """
    import subprocess

    if not BRIDGE_PATH.exists():
        raise ImportError(f"firm_bridge.py not found at {BRIDGE_PATH}")

    report_json = REPO_ROOT / "reports" / "firm_integration.json"
    proc = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "--probe"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise ImportError(
            f"firm_bridge --probe failed rc={proc.returncode}: {proc.stderr.strip()[:200]}"
        )
    if not report_json.exists():
        raise ImportError(f"firm_bridge probe did not emit {report_json}")
    report = json.loads(report_json.read_text(encoding="utf-8"))
    return hashlib.sha256(json.dumps(report, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def verify_shim_contract(*, strict: bool = False) -> ContractProbeResult:
    """Probe the live firm contract and compare against the shim's locked checksum.

    The locked checksum lives in the shim docstring as
    ``Bridge probe checksum: <hex>``. The live checksum is derived by
    importing the firm package and hashing the same surface the bridge
    hashed at generation time.

    Args:
        strict: when True, raise ShimContractDriftError on DRIFT. When
            False (default), return the result for the caller to inspect.

    Returns:
        ContractProbeResult with ``ok`` True if checksums match.
    """
    if not SHIM_PATH.exists():
        result = ContractProbeResult(
            status=ContractStatus.PROBE_FAILED,
            locked_checksum=None,
            live_checksum=None,
            detail=f"shim missing at {SHIM_PATH}",
        )
        if strict:
            raise ShimContractDriftError(result.detail)
        return result

    locked = _extract_locked_checksum(SHIM_PATH.read_text(encoding="utf-8"))

    if locked is None:
        result = ContractProbeResult(
            status=ContractStatus.SHIM_MISSING_CHECKSUM,
            locked_checksum=None,
            live_checksum=None,
            detail=(
                "shim has no `Bridge probe checksum:` line — regenerate via "
                "`python scripts/firm_bridge.py --integrate`"
            ),
        )
        # Treat as advisory; do not block startup on legacy shims.
        return result

    try:
        live = _live_checksum(CONTRACT)
    except ImportError as e:
        result = ContractProbeResult(
            status=ContractStatus.PROBE_FAILED,
            locked_checksum=locked,
            live_checksum=None,
            detail=str(e),
        )
        if strict:
            raise ShimContractDriftError(result.detail) from e
        return result

    if live == locked:
        result = ContractProbeResult(
            status=ContractStatus.OK,
            locked_checksum=locked,
            live_checksum=live,
            detail="contract intact",
        )
        try:
            from mnq._shim_fingerprint_log import log_fingerprint

            log_fingerprint(result)
        except Exception:  # noqa: BLE001
            pass
        return result

    # Drift: the live signature surface differs from what the shim was
    # generated against. The shim's stub functions may now call agent.evaluate
    # with the wrong AgentInput shape, etc.
    detail = (
        f"contract drift: locked={locked} live={live}. "
        "Rerun `python scripts/firm_bridge.py --probe` to inspect, "
        "then `--integrate` to regenerate the shim."
    )
    result = ContractProbeResult(
        status=ContractStatus.DRIFT,
        locked_checksum=locked,
        live_checksum=live,
        detail=detail,
    )
    # Append to the bridge fingerprint history so the operator can see
    # when drift started without spelunking through firm_health logs.
    # Best-effort: never let a logging failure bubble up.
    try:
        from mnq._shim_fingerprint_log import log_fingerprint

        log_fingerprint(result)
    except Exception:  # noqa: BLE001
        pass
    if strict:
        raise ShimContractDriftError(detail)
    return result
