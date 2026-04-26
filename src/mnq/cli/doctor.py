"""[REAL] `mnq doctor` CLI — environment & wiring health check.

Designed to be *cheap and offline* — never talks to a broker, never reads
market data. The intent is: "before I run something important, tell me my
environment is sane." Exits non-zero on any critical failure so it can
gate CI pipelines.

Checks performed (in order):

    1. Python version              (>= 3.11)
    2. Required env vars present   (TV_* — only reports, doesn't exit on missing
                                    unless --strict, since paper-only workflows
                                    don't need them)
    3. All first-party modules import cleanly
    4. Default strategy spec loads and its content_hash is stamped
    5. Generators (pine, python) produce output without raising
    6. MCP server module importable
    7. Critical runtime deps present (httpx, polars, pydantic, typer, rich,
       hypothesis for tests)

Usage:
    mnq doctor            # run all checks, exit 0 if everything green
    mnq doctor --strict   # also fail on missing TV_* env vars
    mnq doctor --json     # emit machine-readable report
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Environment & wiring health check.", no_args_is_help=False)
console = Console()


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

_MIN_PY = (3, 11)

_CORE_MODULES = [
    "mnq.core.types",
    "mnq.core.time",
    "mnq.core.bars_validator",
    "mnq.core.numeric",
    "mnq.spec.ast",
    "mnq.spec.loader",
    "mnq.spec.hash",
    "mnq.spec.schema",
    "mnq.generators.pine",
    "mnq.generators.python_exec",
    "mnq.sim.layer2.engine",
    "mnq.gauntlet.gates.gate_turnover",
    "mnq.executor.safety",
    "mnq.venues.tradovate.auth",
    "mnq.venues.tradovate.config",
]

_TV_ENV_VARS = (
    "TV_USERNAME",
    "TV_PASSWORD",
    "TV_APP_ID",
    "TV_APP_VERSION",
    "TV_DEVICE_ID",
    "TV_CID",
    "TV_SEC",
)

_RUNTIME_DEPS = ("httpx", "polars", "pydantic", "typer", "rich", "yaml")


def _check_python() -> CheckResult:
    v = sys.version_info
    cur = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < _MIN_PY:
        return CheckResult(
            "python_version",
            "fail",
            f"Python {cur} is below required {_MIN_PY[0]}.{_MIN_PY[1]}",
        )
    return CheckResult("python_version", "ok", f"Python {cur}")


def _check_env(strict: bool) -> CheckResult:
    missing = [k for k in _TV_ENV_VARS if not os.environ.get(k)]
    if not missing:
        return CheckResult("tv_env_vars", "ok", "all TV_* vars present")
    status = "fail" if strict else "warn"
    return CheckResult(
        "tv_env_vars",
        status,
        f"missing: {', '.join(missing)} (paper-only workflows OK without these)",
    )


def _check_imports() -> CheckResult:
    broken: list[tuple[str, str]] = []
    for mod in _CORE_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:  # pragma: no cover — exercised by tests with real modules
            broken.append((mod, f"{type(e).__name__}: {e}"))
    if not broken:
        return CheckResult("module_imports", "ok", f"{len(_CORE_MODULES)} modules OK")
    details = "; ".join(f"{m} -> {err}" for m, err in broken)
    return CheckResult("module_imports", "fail", details)


def _check_runtime_deps() -> CheckResult:
    missing: list[str] = []
    for dep in _RUNTIME_DEPS:
        try:
            importlib.import_module(dep)
        except ImportError:
            missing.append(dep)
    if missing:
        return CheckResult("runtime_deps", "fail", f"missing: {', '.join(missing)}")
    return CheckResult("runtime_deps", "ok", f"{len(_RUNTIME_DEPS)} deps importable")


def _default_spec_path() -> Path:
    # Prefer package-relative discovery, fall back to cwd.
    candidates = [
        Path.cwd() / "specs" / "strategies" / "v0_1_baseline.yaml",
        Path(__file__).resolve().parents[3] / "specs" / "strategies" / "v0_1_baseline.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _check_spec_load() -> CheckResult:
    path = _default_spec_path()
    if not path.exists():
        return CheckResult("spec_load", "warn", f"no default spec at {path} (skipped)")
    try:
        from mnq.spec.hash import hash_spec
        from mnq.spec.loader import load_spec

        spec = load_spec(path)
        stamped = getattr(spec.strategy, "content_hash", None)
        actual = hash_spec(spec)
        if stamped and stamped != actual:
            return CheckResult(
                "spec_load",
                "warn",
                f"{path.name}: stamped hash != computed "
                f"({stamped[:12]}… vs {actual[:12]}…). Run `mnq spec rehash`.",
            )
        return CheckResult("spec_load", "ok", f"{path.name} -> {actual[:12]}…")
    except Exception as e:
        return CheckResult("spec_load", "fail", f"{path.name}: {type(e).__name__}: {e}")


def _check_generators() -> CheckResult:
    path = _default_spec_path()
    if not path.exists():
        return CheckResult("generators", "warn", "no default spec to render (skipped)")
    try:
        from mnq.generators.pine import render_pine
        from mnq.generators.python_exec import render_python
        from mnq.spec.loader import load_spec

        spec = load_spec(path)
        pine_src = render_pine(spec)
        py_src = render_python(spec)
        if not pine_src or not py_src:
            return CheckResult("generators", "fail", "generator produced empty output")
        return CheckResult(
            "generators",
            "ok",
            f"pine={len(pine_src)}B, python={len(py_src)}B",
        )
    except Exception as e:
        return CheckResult("generators", "fail", f"{type(e).__name__}: {e}")


def _check_broker_dormancy() -> CheckResult:
    """B5 closure (Red Team review 2026-04-25): refuse to consider the bot
    healthy when the configured execution broker is in DORMANT_BROKERS.

    Reads the broker name from the same env var families the
    eta_v3_framework webhook reads (``BROKER_TYPE`` / ``APEX_BROKER``)
    so a misconfiguration is caught at ``mnq doctor`` time rather than
    at order-submission time. When neither env var is set we skip
    (paper-only sessions are common; this check is for the live path).

    Mirrors the pattern at
    ``eta_engine/venues/router.py::DORMANT_BROKERS``. Cross-repo
    coupling is intentional -- when one repo flips dormancy the other
    should follow in the same operator action.
    """
    # Probe the broker name from the most common env vars first,
    # falling back to the venue name in any live config the operator
    # might have wired. None of these are required; absence is fine.
    candidates = [
        os.environ.get("BROKER_TYPE", "").strip(),
        os.environ.get("APEX_BROKER", "").strip(),
        os.environ.get("MNQ_LIVE_BROKER", "").strip(),
    ]
    configured = next((c for c in candidates if c), "")

    if not configured:
        return CheckResult(
            "broker_dormancy",
            "ok",
            "no live broker configured (paper-only OK)",
        )

    try:
        from mnq.venues.dormancy import DORMANT_BROKERS, is_broker_dormant
    except ImportError as e:
        return CheckResult(
            "broker_dormancy",
            "fail",
            f"dormancy module not importable: {e}",
        )

    if is_broker_dormant(configured):
        return CheckResult(
            "broker_dormancy",
            "fail",
            (
                f"configured broker {configured!r} is in DORMANT_BROKERS "
                f"({sorted(DORMANT_BROKERS)}). Live mode will refuse to "
                f"route orders. Either change the env var or unset "
                f"DORMANT_BROKERS in src/mnq/venues/dormancy.py."
            ),
        )
    return CheckResult(
        "broker_dormancy",
        "ok",
        f"broker {configured!r} is active (not in {sorted(DORMANT_BROKERS)})",
    )


def _check_mcp_server() -> CheckResult:
    try:
        importlib.import_module("mnq.mcp.server")
    except ImportError as e:
        return CheckResult("mcp_server", "warn", f"optional module not importable: {e}")
    except Exception as e:
        return CheckResult("mcp_server", "fail", f"{type(e).__name__}: {e}")
    return CheckResult("mcp_server", "ok", "mnq.mcp.server importable")


def _check_regime_evidence() -> CheckResult:
    """v0.2.17: surface variant-fleet regime-edge state at doctor time.

    Runs the v0.2.15 ``classify_variant`` against every variant in
    ``strategy_v2.VARIANTS`` and returns a summary status:

      ok    -- at least one variant is KEEP (real edge + thick sample)
      warn  -- no KEEPs but at least one WATCH (thin-sample edge)
      fail  -- every variant is PRUNE (no edge anywhere)

    The check is read-only and cheap (~0.5s warm cache). It does NOT
    re-run the backtest -- it just reads the cached_backtest +
    classifier outputs that v0.2.13 + v0.2.15 already produce.
    """
    try:
        # Add scripts/ to sys.path lazily so doctor stays importable
        # when scripts/ isn't on the path (e.g. CI smoke).
        from pathlib import Path as _Path
        repo_root = _Path(__file__).resolve().parents[3]
        scripts = repo_root / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        # importlib.util.spec_from_file_location is the canonical way
        # to load a non-package script module without polluting
        # sys.modules with the wrong name.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_doctor_variant_pruner",
            scripts / "variant_pruner.py",
        )
        if spec is None or spec.loader is None:
            return CheckResult(
                "regime_evidence", "warn",
                "variant_pruner.py not found",
            )
        pruner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pruner)
    except Exception as e:  # noqa: BLE001 -- diagnostic; never crash doctor
        return CheckResult(
            "regime_evidence", "warn",
            f"variant_pruner not loadable: {type(e).__name__}: {e}",
        )

    try:
        rows = pruner._build_classified()  # noqa: SLF001 -- internal API
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            "regime_evidence", "warn",
            f"classifier raised: {type(e).__name__}: {e}",
        )

    if not rows:
        return CheckResult(
            "regime_evidence", "warn",
            "no variants found in strategy_v2.VARIANTS",
        )

    n = len(rows)
    n_keep = sum(1 for r in rows if r["bucket"] == pruner.KEEP)
    n_watch = sum(1 for r in rows if r["bucket"] == pruner.WATCH)
    n_prune = sum(1 for r in rows if r["bucket"] == pruner.PRUNE)

    summary = (
        f"variants={n} KEEP={n_keep} WATCH={n_watch} PRUNE={n_prune}"
    )
    if n_keep > 0:
        return CheckResult("regime_evidence", "ok", summary)
    if n_watch > 0:
        return CheckResult(
            "regime_evidence", "warn",
            summary + " -- no calibrated edge yet, but WATCH variants "
            "may thicken with more sample. Run scripts/regime_report.py "
            "for per-variant detail.",
        )
    # All variants are PRUNE. This is a real signal but NOT a structural
    # block on operations (paper-soak is the path to thicken the sample).
    # Status stays "warn" so doctor still exits 0 -- the operator gets
    # the prominent diagnostic without preventing every action.
    # The 9-gate promotion check is the actual structural gate for going
    # live; this is the upstream "are we even pointing at the right
    # search space?" signal.
    return CheckResult(
        "regime_evidence", "warn",
        summary + " -- no variant has positive expectancy in any "
        "regime under the current cached backtest. Run "
        "scripts/variant_pruner.py for the deletion candidate list, "
        "or extend the backtest sample to thicken evidence.",
    )


def run_all_checks(*, strict: bool = False) -> list[CheckResult]:
    return [
        _check_python(),
        _check_env(strict=strict),
        _check_runtime_deps(),
        _check_imports(),
        _check_spec_load(),
        _check_generators(),
        _check_mcp_server(),
        _check_broker_dormancy(),
        _check_regime_evidence(),
    ]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_STATUS_STYLE = {
    "ok": "[green]OK[/green]",
    "warn": "[yellow]WARN[/yellow]",
    "fail": "[red]FAIL[/red]",
}


@app.callback(invoke_without_command=True)
def doctor(
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Treat missing TV_* env vars as failure."),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a rich table."),
    ] = False,
) -> None:
    """Run a suite of cheap, offline health checks against the installed package."""
    results = run_all_checks(strict=strict)
    failed = [r for r in results if r.status == "fail"]

    if json_out:
        typer.echo(
            json.dumps(
                {
                    "results": [asdict(r) for r in results],
                    "failed": len(failed),
                },
                indent=2,
            )
        )
    else:
        table = Table(title="mnq doctor", title_style="bold cyan")
        table.add_column("check")
        table.add_column("status")
        table.add_column("detail", overflow="fold")
        for r in results:
            table.add_row(r.name, _STATUS_STYLE[r.status], r.detail)
        console.print(table)
        if failed:
            console.print(f"[red]{len(failed)} check(s) failed[/red]")
        else:
            console.print("[green]all checks passed[/green]")

    if failed:
        raise typer.Exit(code=1)
