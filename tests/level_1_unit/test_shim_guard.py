"""Unit tests for the firm_runtime shim self-healing guard.

Verifies:
- Health check detects all four failure modes (missing, empty,
  syntax error, truncated / no-return).
- Health check returns ok on a known-good shim.
- ``ensure_firm_runtime_healthy`` restores a broken file from a
  known-good source.
- When no known-good source is available, it raises (or returns
  based on ``raise_on_failure``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnq._shim_guard import (
    check_file_health,
    check_shim_health,
    ensure_file_healthy,
    ensure_firm_runtime_healthy,
    heal_all_guarded_files,
)

# ---- fixtures ----------------------------------------------------------------

_GOOD_SHIM_CONTENT = '''"""Test firm_runtime shim."""
from __future__ import annotations


def run_six_stage_review(**kwargs):
    outputs = {}
    return outputs
'''


# Same structure as the v5..v9 truncation: for-loop body ends without
# a reachable ``return`` statement.
_TRUNCATED_SHIM_CONTENT = '''"""Truncated firm_runtime shim."""
from __future__ import annotations


def run_six_stage_review(**kwargs):
    outputs = {}
    for k in []:
        outputs[k] = None
        # file ends here -- no return
'''


_SYNTAX_ERROR_CONTENT = '''"""Broken firm_runtime shim."""
from __future__ import annotations


def run_six_stage_review(**kwargs
    # missing closing paren
'''


_MISSING_FN_CONTENT = '''"""Shim without the expected function."""
from __future__ import annotations


def some_other_function():
    return 42
'''


@pytest.fixture
def fake_shim(tmp_path: Path) -> Path:
    return tmp_path / "firm_runtime.py"


# ---- health checks -----------------------------------------------------------


class TestCheckShimHealth:
    def test_missing_file(self, fake_shim: Path):
        h = check_shim_health(fake_shim)
        assert h.ok is False
        assert h.reason == "missing"
        assert h.size_bytes == 0

    def test_empty_file(self, fake_shim: Path):
        fake_shim.write_text("")
        h = check_shim_health(fake_shim)
        assert h.ok is False
        assert h.reason == "empty"

    def test_syntax_error(self, fake_shim: Path):
        fake_shim.write_text(_SYNTAX_ERROR_CONTENT)
        h = check_shim_health(fake_shim)
        assert h.ok is False
        assert h.reason.startswith("syntax_error")

    def test_truncated_no_return(self, fake_shim: Path):
        fake_shim.write_text(_TRUNCATED_SHIM_CONTENT)
        h = check_shim_health(fake_shim)
        assert h.ok is False
        assert h.reason == "truncated_no_return"

    def test_missing_target_function(self, fake_shim: Path):
        fake_shim.write_text(_MISSING_FN_CONTENT)
        h = check_shim_health(fake_shim)
        assert h.ok is False
        assert h.reason == "missing_run_six_stage_review"

    def test_healthy_shim(self, fake_shim: Path):
        fake_shim.write_text(_GOOD_SHIM_CONTENT)
        h = check_shim_health(fake_shim)
        assert h.ok is True
        assert h.reason == "ok"
        assert h.size_bytes > 0


# ---- self-heal behavior ------------------------------------------------------


class TestEnsureFirmRuntimeHealthy:
    def test_noop_when_healthy(self, fake_shim: Path, tmp_path: Path):
        fake_shim.write_text(_GOOD_SHIM_CONTENT)
        original = fake_shim.read_bytes()
        known_good = tmp_path / "known_good.py"
        known_good.write_text(_GOOD_SHIM_CONTENT + "\n# marker comment\n")

        h = ensure_firm_runtime_healthy(fake_shim, known_good=known_good)
        assert h.ok is True
        # File was NOT rewritten (contents unchanged)
        assert fake_shim.read_bytes() == original

    def test_restores_truncated_shim(self, fake_shim: Path, tmp_path: Path):
        fake_shim.write_text(_TRUNCATED_SHIM_CONTENT)
        known_good = tmp_path / "known_good.py"
        known_good.write_text(_GOOD_SHIM_CONTENT)

        h = ensure_firm_runtime_healthy(fake_shim, known_good=known_good)
        assert h.ok is True
        assert fake_shim.read_text() == _GOOD_SHIM_CONTENT

    def test_restores_missing_shim(self, fake_shim: Path, tmp_path: Path):
        assert not fake_shim.exists()
        known_good = tmp_path / "known_good.py"
        known_good.write_text(_GOOD_SHIM_CONTENT)

        h = ensure_firm_runtime_healthy(fake_shim, known_good=known_good)
        assert h.ok is True
        assert fake_shim.exists()

    def test_raises_when_broken_and_no_known_good(self, fake_shim: Path):
        fake_shim.write_text(_TRUNCATED_SHIM_CONTENT)
        missing = fake_shim.parent / "does_not_exist.py"

        with pytest.raises(RuntimeError, match="firm_runtime.py is broken"):
            ensure_firm_runtime_healthy(fake_shim, known_good=missing)

    def test_returns_failure_when_raise_disabled(self, fake_shim: Path):
        fake_shim.write_text(_TRUNCATED_SHIM_CONTENT)
        missing = fake_shim.parent / "does_not_exist.py"

        h = ensure_firm_runtime_healthy(
            fake_shim,
            known_good=missing,
            raise_on_failure=False,
        )
        assert h.ok is False
        assert h.reason == "truncated_no_return"

    def test_raises_when_known_good_is_itself_corrupt(
        self,
        fake_shim: Path,
        tmp_path: Path,
    ):
        fake_shim.write_text(_TRUNCATED_SHIM_CONTENT)
        corrupt = tmp_path / "corrupt.py"
        corrupt.write_text(_TRUNCATED_SHIM_CONTENT)

        with pytest.raises(RuntimeError, match="itself be corrupt"):
            ensure_firm_runtime_healthy(fake_shim, known_good=corrupt)

    def test_atomic_write_cleans_up_tmp(self, fake_shim: Path, tmp_path: Path):
        fake_shim.write_text(_TRUNCATED_SHIM_CONTENT)
        known_good = tmp_path / "known_good.py"
        known_good.write_text(_GOOD_SHIM_CONTENT)

        ensure_firm_runtime_healthy(fake_shim, known_good=known_good)

        # Ensure no leftover .repair.tmp
        leftover = fake_shim.with_suffix(".py.repair.tmp")
        assert not leftover.exists()


# ---- generic file-health guard ----------------------------------------------

_GOOD_INIT_CONTENT = '''"""Fake __init__.py module."""
from __future__ import annotations

from .sub import foo, bar, baz

__all__ = ["foo", "bar", "baz"]
'''

_TRUNCATED_INIT_CONTENT = '''"""Fake __init__.py module, truncated mid-import."""
from __future__ import annotations

from .sub import foo
'''


class TestCheckFileHealth:
    def test_missing_file(self, tmp_path: Path):
        h = check_file_health(tmp_path / "nope.py", required_symbols=("foo",))
        assert h.ok is False
        assert h.reason == "missing"

    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.py"
        p.write_text("")
        h = check_file_health(p, required_symbols=("foo",))
        assert h.ok is False
        assert h.reason == "empty"

    def test_too_small(self, tmp_path: Path):
        p = tmp_path / "tiny.py"
        p.write_text("x = 1\n")
        h = check_file_health(p, required_symbols=("x",), min_bytes=500)
        assert h.ok is False
        assert h.reason.startswith("too_small")

    def test_syntax_error(self, tmp_path: Path):
        p = tmp_path / "broken.py"
        p.write_text("def x(:\n")
        h = check_file_health(p, required_symbols=("x",))
        assert h.ok is False
        assert h.reason.startswith("syntax_error")

    def test_missing_required_symbols(self, tmp_path: Path):
        p = tmp_path / "short.py"
        p.write_text(_TRUNCATED_INIT_CONTENT)
        h = check_file_health(p, required_symbols=("foo", "bar", "baz"))
        assert h.ok is False
        assert h.reason.startswith("missing_symbols")
        # foo imported, bar + baz are the truncated tail
        assert "bar" in h.reason
        assert "baz" in h.reason
        assert "foo" not in h.reason.split("missing_symbols: ", 1)[1]

    def test_healthy_init(self, tmp_path: Path):
        p = tmp_path / "ok.py"
        p.write_text(_GOOD_INIT_CONTENT)
        h = check_file_health(p, required_symbols=("foo", "bar", "baz"))
        assert h.ok is True
        assert h.reason == "ok"

    def test_detects_class_and_function_defs(self, tmp_path: Path):
        p = tmp_path / "mod.py"
        p.write_text("class Foo: ...\ndef bar(): return 1\nBAZ: int = 3\n")
        h = check_file_health(p, required_symbols=("Foo", "bar", "BAZ"))
        assert h.ok is True


class TestEnsureFileHealthy:
    def test_noop_when_healthy(self, tmp_path: Path):
        target = tmp_path / "good.py"
        target.write_text(_GOOD_INIT_CONTENT)
        known_good = tmp_path / "kg.py"
        known_good.write_text(_GOOD_INIT_CONTENT + "# extra\n")
        original = target.read_bytes()

        h = ensure_file_healthy(
            target,
            known_good=known_good,
            required_symbols=("foo", "bar", "baz"),
        )
        assert h.ok is True
        assert target.read_bytes() == original

    def test_restores_truncated_file(self, tmp_path: Path):
        target = tmp_path / "bad.py"
        target.write_text(_TRUNCATED_INIT_CONTENT)
        known_good = tmp_path / "kg.py"
        known_good.write_text(_GOOD_INIT_CONTENT)

        h = ensure_file_healthy(
            target,
            known_good=known_good,
            required_symbols=("foo", "bar", "baz"),
        )
        assert h.ok is True
        assert target.read_text() == _GOOD_INIT_CONTENT

    def test_raises_when_no_known_good(self, tmp_path: Path):
        target = tmp_path / "bad.py"
        target.write_text(_TRUNCATED_INIT_CONTENT)

        with pytest.raises(RuntimeError, match="is broken"):
            ensure_file_healthy(
                target,
                known_good=None,
                required_symbols=("foo", "bar", "baz"),
            )

    def test_returns_failure_when_raise_disabled(self, tmp_path: Path):
        target = tmp_path / "bad.py"
        target.write_text(_TRUNCATED_INIT_CONTENT)

        h = ensure_file_healthy(
            target,
            known_good=None,
            required_symbols=("foo", "bar", "baz"),
            raise_on_failure=False,
        )
        assert h.ok is False


class TestMidFunctionTruncation:
    """Guard against the OneDrive 'body truncated before final return' bug.

    The full function has both an early guard return AND a final return.
    When OneDrive lops off the tail, the early return survives but the
    main flow + final return are gone. The weaker ``has_return`` check
    passes; only the ``last-stmt-is-Return`` check catches it.
    """

    _FULL = """
def process(x):
    if x is None:
        return None
    y = x + 1
    return y
"""

    _TRUNCATED_MID_BODY = """
def process(x):
    if x is None:
        return None
    y = x + 1
"""

    def test_full_function_is_healthy(self, tmp_path: Path):
        p = tmp_path / "mod.py"
        p.write_text(self._FULL)
        h = check_file_health(
            p,
            required_symbols=("process",),
            required_fn_returns=("process",),
        )
        assert h.ok is True

    def test_mid_body_truncation_is_caught(self, tmp_path: Path):
        p = tmp_path / "mod.py"
        p.write_text(self._TRUNCATED_MID_BODY)
        h = check_file_health(
            p,
            required_symbols=("process",),
            required_fn_returns=("process",),
        )
        assert h.ok is False
        assert "truncated_body_missing_final_return" in h.reason

    def test_if_else_both_returning_passes(self, tmp_path: Path):
        p = tmp_path / "mod.py"
        p.write_text("""
def process(x):
    if x:
        return 1
    else:
        return 2
""")
        h = check_file_health(
            p,
            required_symbols=("process",),
            required_fn_returns=("process",),
        )
        assert h.ok is True


class TestHealAllGuardedFiles:
    def test_smoke_returns_health_map(self):
        # Exercises the live registry against the actual repo state —
        # after v10 the files should all be healthy; we just check the
        # shape and that firm_runtime.py is reported.
        result = heal_all_guarded_files(raise_on_failure=False)
        assert "firm_runtime.py" in result
        assert all(hasattr(h, "ok") for h in result.values())
        # Every guarded file should be healthy in a clean repo state.
        broken = {name: h.reason for name, h in result.items() if not h.ok}
        assert not broken, f"Guarded files broken: {broken}"
