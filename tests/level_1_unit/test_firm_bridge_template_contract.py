"""Pin SHIM_TEMPLATE contract: every public API symbol that any consumer
imports from ``mnq.firm_runtime`` MUST appear in
``scripts/firm_bridge.py::SHIM_TEMPLATE``.

Why this exists
---------------
The firm runtime shim (``src/mnq/firm_runtime.py``) is auto-generated
by ``scripts/firm_bridge.py``. Editing the generated file directly is a
trap: the next bridge regeneration overwrites the file from
``SHIM_TEMPLATE`` and any hand-edits are lost.

This bit us in v0.2.1 -> v0.2.2: the B4 stub ``record_trade_outcome``
was added to the live shim in v0.2.1, but the operator's auto-process
ran the bridge against the new ``C:/Users/edwar/projects/firm`` package
on 2026-04-26 and regenerated the shim from a SHIM_TEMPLATE that did
NOT contain ``record_trade_outcome`` -- silently un-fixing the B4
closure. The fix (97bd085) added the stub to SHIM_TEMPLATE so future
regens preserve it.

This test makes that fix durable: any future contributor who edits
``firm_runtime.py`` directly (instead of the template) will see this
test fail and learn to update the template.

What's pinned
-------------
* Every name in the manually-curated ``_PUBLIC_API`` list below MUST
  appear in the rendered template output.
* The rendered template MUST be valid Python (no syntax errors from
  brace-escaping bugs etc.).
* The rendered template MUST be importable in a sub-interpreter (no
  obvious side-effect breakage like the wrong sys.path order).

When the public API legitimately gains a new symbol
---------------------------------------------------
  1. Add the symbol to the SHIM_TEMPLATE in firm_bridge.py.
  2. Add the same symbol to ``_PUBLIC_API`` below.
  3. Re-run this test.

When the public API loses a symbol (rare)
-----------------------------------------
  1. Remove from SHIM_TEMPLATE.
  2. Remove from ``_PUBLIC_API`` below.
  3. Search-and-confirm no caller imports the removed symbol.
  4. Re-run this test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRM_BRIDGE_PATH = REPO_ROOT / "scripts" / "firm_bridge.py"


# Every public name any consumer imports from ``mnq.firm_runtime``.
# Source-of-truth audit: ``grep -rn "from mnq.firm_runtime import"
# --include="*.py"`` across the repo, then dedupe + sort.
_PUBLIC_API: list[str] = [
    "compute_confluence",
    "record_trade_outcome",
    "run_six_stage_review",
]


@pytest.fixture(scope="module")
def shim_template() -> str:
    """Load SHIM_TEMPLATE from firm_bridge.py via importlib.

    We deliberately don't ``import scripts.firm_bridge`` because
    the ``scripts`` directory isn't a package. Registering the
    module in ``sys.modules`` is necessary so that any
    ``@dataclass`` decorators inside firm_bridge can resolve their
    own module via ``cls.__module__``.
    """
    spec = importlib.util.spec_from_file_location(
        "firm_bridge_for_test",
        FIRM_BRIDGE_PATH,
    )
    if spec is None or spec.loader is None:
        msg = f"could not build module spec for {FIRM_BRIDGE_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["firm_bridge_for_test"] = module
    try:
        spec.loader.exec_module(module)
        return module.SHIM_TEMPLATE
    finally:
        # Leave the module in sys.modules for the rest of the test
        # session -- removing it would invalidate the dataclass-tied
        # type identity inside it.
        pass


@pytest.fixture(scope="module")
def rendered_template(shim_template: str) -> str:
    """Render the template with placeholder substitutions."""
    return shim_template.format(
        generated_at="2026-04-26T00:00:00Z",
        checksum="deadbeefdeadbeef",
        firm_pkg_parent="C:/Users/edwar/projects",
    )


# ---------------------------------------------------------------------------
# Contract: every public API name is present in the rendered template
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", _PUBLIC_API)
def test_template_defines_public_api_symbol(
    rendered_template: str,
    symbol: str,
) -> None:
    """The rendered shim must define every name callers import.

    A failure here means somebody refactored SHIM_TEMPLATE and dropped
    a ``def {symbol}(...)`` or ``{symbol} = ...`` line that callers
    rely on. The next ``firm_bridge.py --integrate`` would regenerate
    the live shim into a state where consumer imports fail (silently,
    if the import is wrapped in try/except as ``live_sim.py:337``
    used to do for ``record_trade_outcome``).
    """
    # We look for a definition pattern (def or top-level assignment)
    # rather than a bare substring match -- a docstring mention of the
    # symbol shouldn't satisfy the contract.
    patterns = [
        f"def {symbol}(",
        f"\n{symbol} = ",
        f"async def {symbol}(",
    ]
    found = any(p in rendered_template for p in patterns)
    assert found, (
        f"public API symbol {symbol!r} is not defined in "
        f"SHIM_TEMPLATE (scripts/firm_bridge.py). The next bridge "
        f"regeneration would produce a shim missing this symbol -- "
        f"any caller doing ``from mnq.firm_runtime import {symbol}`` "
        f"would silently fail (when wrapped in try/except) or raise "
        f"ImportError. Add a ``def {symbol}(...)`` to SHIM_TEMPLATE."
    )


# ---------------------------------------------------------------------------
# Structural integrity: the rendered template must be valid Python
# ---------------------------------------------------------------------------


def test_rendered_template_is_valid_python(rendered_template: str) -> None:
    """Compile the rendered output. Catches brace-escaping bugs in
    SHIM_TEMPLATE that ship valid-looking text but break the syntax
    of the generated shim.
    """
    try:
        compile(rendered_template, "<rendered SHIM_TEMPLATE>", "exec")
    except SyntaxError as exc:
        pytest.fail(
            f"SHIM_TEMPLATE renders to invalid Python: {exc}.\n"
            f"This usually means a `{{` or `}}` literal in the "
            f"template was not properly escaped (single braces are "
            f"format-string substitutions; literal braces need `{{{{` "
            f"and `}}}}`)."
        )


def test_rendered_template_has_required_structural_pieces(
    rendered_template: str,
) -> None:
    """Sanity-check that the obvious must-have lines survived
    rendering (sys.path manipulation, stage tuple, type hints).
    """
    must_contain = [
        "_FIRM_PACKAGE_PARENT = Path(",
        "if str(_FIRM_PACKAGE_PARENT) not in sys.path:",
        "sys.path.insert(0, str(_FIRM_PACKAGE_PARENT))",
        "from firm.agents.base import AgentInput",
        "from firm.agents.core import",
        "_STAGES = (",
    ]
    for piece in must_contain:
        assert piece in rendered_template, (
            f"SHIM_TEMPLATE is missing required structural piece: "
            f"{piece!r}. This is the kind of break that surfaces as "
            f"every consumer of mnq.firm_runtime failing at import "
            f"time after the next bridge regen."
        )


# ---------------------------------------------------------------------------
# Sub-interpreter import test: load the rendered shim and verify the
# public API is callable.
# ---------------------------------------------------------------------------


def test_rendered_shim_loads_and_exposes_api(
    tmp_path: Path,
    rendered_template: str,
) -> None:
    """Write the rendered shim to a temp file and import it in a
    sub-context. Asserts every _PUBLIC_API name is bound to a callable
    after import. Catches the case where a symbol APPEARS in the
    template (passing the parametrized test above) but is unreachable
    because of a typo or a guard / try-except that silently rebinds it.
    """
    shim_dir = tmp_path / "test_shim_pkg"
    shim_dir.mkdir()
    (shim_dir / "__init__.py").write_text("", encoding="utf-8")

    # Rewrite the firm-package import to point at a stub the test ships,
    # so we don't need the real ``firm`` package on PYTHONPATH for this
    # contract test.
    stub = '''"""Stub firm package for SHIM_TEMPLATE contract test."""

class _StubAgent:
    def evaluate(self, _in):  # noqa: ARG002
        return {"verdict": "stub"}


class QuantAgent(_StubAgent): pass
class RedTeamAgent(_StubAgent): pass
class RiskManagerAgent(_StubAgent): pass
class MacroAgent(_StubAgent): pass
class MicrostructureAgent(_StubAgent): pass
class PMAgent(_StubAgent): pass


class AgentInput:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
'''
    firm_pkg = tmp_path / "firm"
    firm_pkg.mkdir()
    (firm_pkg / "__init__.py").write_text("", encoding="utf-8")
    agents_dir = firm_pkg / "agents"
    agents_dir.mkdir()
    (agents_dir / "__init__.py").write_text("", encoding="utf-8")
    (agents_dir / "base.py").write_text(stub, encoding="utf-8")
    (agents_dir / "core.py").write_text(stub, encoding="utf-8")

    # Reuse the module-scoped firm_bridge import registered by
    # the shim_template fixture. We can't construct a fresh module
    # here without re-executing firm_bridge.py (slow + side-effecty).
    bridge = sys.modules.get("firm_bridge_for_test")
    if bridge is None:
        spec = importlib.util.spec_from_file_location(
            "firm_bridge_for_test",
            FIRM_BRIDGE_PATH,
        )
        assert spec is not None and spec.loader is not None
        bridge = importlib.util.module_from_spec(spec)
        sys.modules["firm_bridge_for_test"] = bridge
        spec.loader.exec_module(bridge)
    rendered = bridge.SHIM_TEMPLATE.format(
        generated_at="2026-04-26T00:00:00Z",
        checksum="deadbeefdeadbeef",
        firm_pkg_parent=str(tmp_path),
    )
    shim_path = shim_dir / "shim.py"
    shim_path.write_text(rendered, encoding="utf-8")

    # Import the shim in a sub-spec so we don't pollute sys.modules
    # globally.
    shim_spec = importlib.util.spec_from_file_location(
        "test_firm_runtime_shim",
        shim_path,
    )
    assert shim_spec is not None and shim_spec.loader is not None
    shim_mod = importlib.util.module_from_spec(shim_spec)
    # The shim mutates sys.path to include firm_pkg_parent. Track
    # the original to restore.
    orig_path = list(sys.path)
    try:
        shim_spec.loader.exec_module(shim_mod)
        for symbol in _PUBLIC_API:
            assert hasattr(shim_mod, symbol), (
                f"rendered shim is missing {symbol!r} after import. "
                f"The symbol appears in SHIM_TEMPLATE source but is "
                f"not bound at module level -- check for syntax / "
                f"try-except / conditional-define traps."
            )
            attr = getattr(shim_mod, symbol)
            assert callable(attr), (
                f"rendered shim's {symbol} is not callable: {attr!r}. "
                f"Public API contract requires functions, not "
                f"variables."
            )
    finally:
        sys.path[:] = orig_path
