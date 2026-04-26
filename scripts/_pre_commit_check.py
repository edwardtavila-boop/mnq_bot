"""Pre-commit hygiene gate for mnq_bot.

Runs ruff + pytest before any commit and refuses to let the commit
proceed if either fails. Mirrors eta_engine's gate but uses uv
since mnq_bot is uv-managed.

Exit codes:
  0 -> all checks passed, commit may proceed
  1 -> ruff failed
  2 -> pytest failed
  3 -> setup error (e.g. uv not installed)

Usage
-----
Direct:

    python scripts/_pre_commit_check.py

As a git pre-commit hook (one-time install):

    python scripts/_pre_commit_check.py --install-hook

The hook lints staged .py files only (legacy code in scripts/ that the
operator hasn't been maintaining shouldn't gate fresh work).
"""
from __future__ import annotations

import argparse
import contextlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK_BODY = """#!/bin/sh
# mnq_bot pre-commit hygiene gate (auto-installed)
exec python scripts/_pre_commit_check.py
"""


def _run(cmd: list[str], *, cwd: Path) -> int:
    print(f"  $ {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, cwd=cwd, check=False)
    return proc.returncode


def _staged_python_files(*, root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return []
    return [
        line for line in out.stdout.splitlines()
        if line.endswith(".py") and (root / line).exists()
    ]


def _have_uv() -> bool:
    return shutil.which("uv") is not None


def _ruff_check(*, root: Path) -> int:
    files = _staged_python_files(root=root)
    if not files:
        print("[pre-commit] no staged .py files; skipping ruff", file=sys.stderr)
        return 0
    cmd = (
        ["uv", "tool", "run", "ruff", "check", *files]
        if _have_uv()
        else ["python", "-m", "ruff", "check", *files]
    )
    rc = _run(cmd, cwd=root)
    if rc != 0:
        print(
            f"[pre-commit] FAIL: ruff found issues in "
            f"{len(files)} staged file(s)",
            file=sys.stderr,
        )
    return rc


def _pytest_check(*, root: Path) -> int:
    cmd = (
        ["uv", "run", "pytest", "-x", "-q", "--no-header"]
        if _have_uv()
        else ["python", "-m", "pytest", "-x", "-q", "--no-header"]
    )
    rc = _run(cmd, cwd=root)
    if rc != 0:
        print("[pre-commit] FAIL: pytest reports broken tests", file=sys.stderr)
    return rc


def _install_hook(*, root: Path) -> int:
    hooks_dir = root / ".git" / "hooks"
    if not hooks_dir.exists():
        print(
            f"[pre-commit] cannot install: {hooks_dir} does not exist",
            file=sys.stderr,
        )
        return 3
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text(HOOK_BODY, encoding="utf-8")
    with contextlib.suppress(OSError):
        hook_path.chmod(0o755)
    print(f"[pre-commit] installed -> {hook_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--install-hook", action="store_true")
    p.add_argument("--quick", action="store_true",
                   help="skip pytest (only run ruff)")
    p.add_argument("--no-pytest", action="store_true",
                   help="skip pytest with a loud warning")
    args = p.parse_args(argv)

    if args.install_hook:
        return _install_hook(root=ROOT)

    print("[pre-commit] running ruff...", file=sys.stderr)
    rc = _ruff_check(root=ROOT)
    if rc != 0:
        return 1

    skip_pytest = args.quick or args.no_pytest
    if args.quick:
        print("[pre-commit] --quick -> skipping pytest (ruff passed)", file=sys.stderr)
    elif args.no_pytest:
        print(
            "[pre-commit] --no-pytest -> WARNING: skipping pytest, "
            "you are committing untested code",
            file=sys.stderr,
        )
    else:
        print("[pre-commit] running pytest...", file=sys.stderr)
        rc = _pytest_check(root=ROOT)
        if rc != 0:
            return 2

    # Advisory audits -- mirrors eta_engine's pre-commit pattern.
    # Audit failures do NOT block the commit; they print summaries
    # inline so the operator sees drift at commit time. Audits run
    # even in --quick / --no-pytest mode (cheap, sub-second). To
    # promote any audit to a hard gate, change the call site to
    # inspect the return code and return non-zero.
    _ = skip_pytest  # documentation marker for the comment above
    _advisory_audits(root=ROOT)

    print("[pre-commit] OK -- commit may proceed", file=sys.stderr)
    return 0


def _advisory_audits(*, root: Path) -> None:
    """Run audit scripts in advisory mode and surface results.

    Failures here do NOT block the commit. They print to stderr so
    the operator sees them inline. Mirrors
    eta_engine/scripts/_pre_commit_check.py::_advisory_audits.
    """
    audits = [
        ("spec-vs-code", "scripts/_audit_spec_vs_code.py"),
        ("deferral-criteria", "scripts/_audit_deferral_criteria.py"),
    ]
    for label, script in audits:
        path = root / script
        if not path.exists():
            print(
                f"[pre-commit] advisory: {label} -- {script} missing, skipping",
                file=sys.stderr,
            )
            continue
        print(f"[pre-commit] advisory: {label}...", file=sys.stderr)
        result = subprocess.run(
            ["python", str(path)],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            tail = result.stdout.rstrip().splitlines()[-15:]
            print(
                f"[pre-commit] advisory: {label} reports issues "
                f"(rc={result.returncode}, NOT blocking):",
                file=sys.stderr,
            )
            for line in tail:
                print(f"[pre-commit]   {line}", file=sys.stderr)
        else:
            lines = [
                ln for ln in result.stdout.rstrip().splitlines()
                if ln.strip()
            ]
            if lines:
                print(f"[pre-commit]   {lines[-1]}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
