"""Root pytest conftest for EVOLUTIONARY TRADING ALGO.

Side effect (intentional): import-time self-heal of the
``firm_runtime.py`` bridge shim. OneDrive has truncated the shim in
five separate batches (v5..v9). Rather than fix each time by hand, the
root conftest now calls ``ensure_firm_runtime_healthy()`` before any
test module is collected, restoring the shim from the checked-in
known-good copy if it's broken.

This is a *guard*, not a regenerator — it does not invoke firm_bridge.
Once the OneDrive sync artifact is fixed (either by relocating
firm_runtime.py out of the synced tree, or write-protecting it), this
module becomes a no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is on sys.path so we can import mnq._shim_guard without
# the usual src-layout trickery.
_REPO_ROOT = Path(__file__).resolve().parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from mnq._shim_guard import heal_all_guarded_files
except Exception:
    # If the guard itself fails to import, fall through silently —
    # tests that need the shim will fail loudly on their own, and
    # this file must never bring down collection on an unrelated issue.
    pass
else:
    # Sweep the shim plus every OneDrive-truncation-prone file. This
    # covers firm_runtime.py (v5..v9 truncations), eta_v3/__init__.py
    # (v9 truncation), and eta_v3/meta_adapter.py (v10 truncation).
    heal_all_guarded_files(raise_on_failure=False)
