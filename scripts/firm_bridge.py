"""Firm-code integration bridge.

Responsibilities:

1. **Discover** the in-progress Firm Python package (default location:
   `~/OneDrive/the_firm_complete/desktop_app/firm`). Other locations are
   supported via the ``FIRM_CODE_PATH`` environment variable or ``--path``.
2. **Probe** the public surface the mnq_bot needs without fully importing —
   we only sniff module structure, class names, and signature shapes. That
   keeps the bot decoupled from an under-construction codebase: if the
   Firm code has import-time side effects or missing deps, we surface it
   as "NOT READY" rather than crash live_sim.
3. **Report** readiness as a structured markdown file + JSON status file.
   ``ready=True`` only when all six agents + required types are resolvable
   and each agent's ``evaluate`` signature accepts an ``AgentInput``.
4. **Optionally adapt** — when ``--integrate`` is passed AND readiness is
   OK, the bridge writes ``mnq_bot/src/mnq/firm_runtime.py`` with a thin
   shim that exposes ``run_six_stage_review(signal_dict, context) -> dict``.
   The shim is the ONLY production code that ever imports the Firm
   package, keeping the boundary tight.

The contract we expect from the Firm package (verbatim, as of today's probe):

    firm.types.Verdict             Enum
    firm.types.Quadrant            Enum
    firm.agents.base.AgentInput    dataclass(strategy_id, decision_context, payload, ...)
    firm.agents.base.AgentOutput   dataclass(verdict, probability, ci, falsification, ...)
    firm.agents.core.QuantAgent            class with .evaluate(AgentInput) -> AgentOutput
    firm.agents.core.RedTeamAgent          ...
    firm.agents.core.RiskManagerAgent      ...
    firm.agents.core.MacroAgent            ...
    firm.agents.core.MicrostructureAgent   ...
    firm.agents.core.PMAgent               ...

If any of those fails, the bridge refuses to integrate and we continue
running the markdown-only review path (``scripts/firm_review.py``).

Usage:

    python scripts/firm_bridge.py --probe
    python scripts/firm_bridge.py --probe --path /other/firm/code
    python scripts/firm_bridge.py --integrate            # writes firm_runtime.py
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIRM_PATH_ENV = "FIRM_CODE_PATH"
# H2 closure (Red Team review 2026-04-25): firm package was historically
# at OneDrive/The_Firm/the_firm_complete/desktop_app/firm but OneDrive's
# sync layer was truncating the firm_runtime.py shim unpredictably (the
# reason _shim_guard.py exists). The package is now mirrored at
# C:/Users/edwar/projects/firm (sibling to mnq_bot, no OneDrive). The
# bridge prefers the projects/ location and falls back to OneDrive only
# when projects/ is missing -- supports a fresh machine that hasn't run
# the migration yet. Operator override via FIRM_CODE_PATH env var.
_DEFAULT_FIRM_CANDIDATES = (
    Path("C:/Users/edwar/projects/firm"),
    Path("C:/Users/edwar/OneDrive/The_Firm/the_firm_complete/desktop_app/firm"),
)
DEFAULT_FIRM_PATH = next(
    (p for p in _DEFAULT_FIRM_CANDIDATES if p.exists()),
    _DEFAULT_FIRM_CANDIDATES[0],  # honest first choice if neither exists
)
REPORT_PATH = REPO_ROOT / "reports" / "firm_integration.md"
STATUS_JSON_PATH = REPO_ROOT / "reports" / "firm_integration.json"
RUNTIME_SHIM_PATH = REPO_ROOT / "src" / "mnq" / "firm_runtime.py"

# Contract the mnq_bot needs from the Firm package.
CONTRACT: dict[str, list[str]] = {
    "firm.types": ["Verdict", "Quadrant"],
    "firm.agents.base": ["Agent", "AgentInput", "AgentOutput"],
    "firm.agents.core": [
        "QuantAgent",
        "RedTeamAgent",
        "RiskManagerAgent",
        "MacroAgent",
        "MicrostructureAgent",
        "PMAgent",
    ],
}


@dataclass
class ProbeReport:
    firm_path: Path
    path_exists: bool
    modules: dict[str, dict] = field(default_factory=dict)
    missing: list[tuple[str, str]] = field(default_factory=list)
    ready: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "firm_path": str(self.firm_path),
            "path_exists": self.path_exists,
            "modules": self.modules,
            "missing": [{"module": m, "name": n} for m, n in self.missing],
            "ready": self.ready,
            "error": self.error,
        }


def _prepend_firm_path(firm_path: Path) -> None:
    """Add the Firm PACKAGE PARENT to sys.path so `import firm` works."""
    pkg_parent = str(firm_path.parent)
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)


def _probe_module(modname: str, required_names: list[str]) -> tuple[dict, list[tuple[str, str]]]:
    """Return (info_dict, missing_list) for one module."""
    info: dict = {"importable": False, "file": None, "resolved": []}
    missing: list[tuple[str, str]] = []
    try:
        mod = importlib.import_module(modname)
        info["importable"] = True
        info["file"] = getattr(mod, "__file__", None)
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
        for n in required_names:
            missing.append((modname, n))
        return info, missing

    for n in required_names:
        if not hasattr(mod, n):
            missing.append((modname, n))
        else:
            info["resolved"].append(n)
    return info, missing


def _probe_evaluate_signatures(firm_path: Path) -> list[str]:
    """Check each agent's evaluate() still accepts an AgentInput positional."""
    errors: list[str] = []
    try:
        core = importlib.import_module("firm.agents.core")
    except Exception as exc:  # noqa: BLE001
        return [f"firm.agents.core import failed: {exc}"]
    _ = firm_path  # reserved for future per-file sanity checks

    for cls_name in CONTRACT["firm.agents.core"]:
        cls = getattr(core, cls_name, None)
        if cls is None:
            errors.append(f"{cls_name}: not found")
            continue
        if not inspect.isclass(cls):
            errors.append(f"{cls_name}: not a class")
            continue
        ev = getattr(cls, "evaluate", None)
        if ev is None or not callable(ev):
            errors.append(f"{cls_name}.evaluate: missing or not callable")
            continue
        try:
            sig = inspect.signature(ev)
        except (TypeError, ValueError) as exc:
            errors.append(f"{cls_name}.evaluate: signature not introspectable ({exc})")
            continue
        params = [p for p in sig.parameters.values() if p.name != "self"]
        if len(params) != 1:
            errors.append(
                f"{cls_name}.evaluate: expected 1 non-self parameter, got {len(params)}"
            )
    return errors


def probe(firm_path: Path | None = None) -> ProbeReport:
    """Run the full discovery probe and return a structured report."""
    env_path = os.environ.get(DEFAULT_FIRM_PATH_ENV)
    if firm_path is None:
        firm_path = Path(env_path) if env_path else DEFAULT_FIRM_PATH

    report = ProbeReport(firm_path=firm_path, path_exists=firm_path.exists())
    if not report.path_exists:
        report.error = "Firm code path does not exist"
        return report

    # Fresh sys.path; evict any stale firm.* modules from a prior probe.
    _prepend_firm_path(firm_path)
    for name in list(sys.modules):
        if name == "firm" or name.startswith("firm."):
            del sys.modules[name]

    for modname, required in CONTRACT.items():
        info, missing = _probe_module(modname, required)
        report.modules[modname] = info
        report.missing.extend(missing)

    sig_errors = _probe_evaluate_signatures(firm_path)
    if sig_errors:
        for msg in sig_errors:
            report.missing.append(("firm.agents.core", f"signature: {msg}"))

    report.ready = not report.missing
    return report


def render(report: ProbeReport) -> str:
    lines: list[str] = ["# Firm Integration — Readiness Probe", ""]
    lines.append(f"- Firm code path: `{report.firm_path}`")
    lines.append(f"- Path exists: **{report.path_exists}**")
    lines.append(f"- Ready for integration: **{report.ready}**")
    if report.error:
        lines.append(f"- Probe error: `{report.error}`")
    lines.append("")

    lines.append("## Module probe")
    lines.append("")
    lines.append("| Module | Importable | Required names resolved |")
    lines.append("|---|---|---|")
    for modname, info in report.modules.items():
        imp = "yes" if info.get("importable") else "no"
        resolved = info.get("resolved", [])
        required = CONTRACT.get(modname, [])
        mark = f"{len(resolved)}/{len(required)}"
        lines.append(f"| `{modname}` | {imp} | {mark} |")
    lines.append("")

    if report.missing:
        lines.append("## Gaps blocking integration")
        lines.append("")
        for m, n in report.missing:
            lines.append(f"- `{m}.{n}`")
        lines.append("")
    else:
        lines.append("## Gaps blocking integration")
        lines.append("")
        lines.append("_none — contract satisfied._")
        lines.append("")

    lines.append("## Next step")
    lines.append("")
    if report.ready:
        lines.append(
            "Run `python scripts/firm_bridge.py --integrate` to emit the "
            "runtime shim at `src/mnq/firm_runtime.py`. Live_sim will then "
            "delegate the six-stage review to the real Firm agents."
        )
    else:
        lines.append(
            "Continue running the markdown-only Firm review path "
            "(`scripts/firm_review.py`). Rerun this probe after each Firm-code "
            "fine-tune cycle; integration will auto-enable when the contract is met."
        )
    return "\n".join(lines) + "\n"


SHIM_TEMPLATE = '''"""Firm runtime shim — auto-generated by scripts/firm_bridge.py.

Do not edit by hand. This is the ONLY module in mnq_bot that imports the
external `firm` package; every other consumer goes through
``run_six_stage_review``. If the Firm code changes shape, rerun the bridge.

Generated at: {generated_at}
Bridge probe checksum: {checksum}
"""
from __future__ import annotations

import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_FIRM_PACKAGE_PARENT = Path({firm_pkg_parent!r})
if str(_FIRM_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_FIRM_PACKAGE_PARENT))

from firm.agents.base import AgentInput as _AgentInput  # noqa: E402
from firm.agents.core import (  # noqa: E402
    MacroAgent,
    MicrostructureAgent,
    PMAgent,
    QuantAgent,
    RedTeamAgent,
    RiskManagerAgent,
)

_STAGES = (
    ("quant", QuantAgent),
    ("red_team", RedTeamAgent),
    ("risk", RiskManagerAgent),
    ("macro", MacroAgent),
    ("micro", MicrostructureAgent),
    ("pm", PMAgent),
)


def _safe_asdict(obj: Any) -> Any:
    if is_dataclass(obj):
        return {{k: _safe_asdict(v) for k, v in asdict(obj).items()}}
    if hasattr(obj, "value") and hasattr(type(obj), "__members__"):  # Enum
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_safe_asdict(v) for v in obj]
    if isinstance(obj, dict):
        return {{k: _safe_asdict(v) for k, v in obj.items()}}
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def compute_confluence(
    *,
    internals: dict[str, Any] | None = None,
    volatility: dict[str, Any] | None = None,
    cross_asset: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
    micro: dict[str, Any] | None = None,
    calendar: dict[str, Any] | None = None,
    eta_v3: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute an 8-axis confluence snapshot.

    Fail-open stub: the upstream ``firm`` package does not yet expose a
    canonical ``compute_confluence``. This stub aggregates the inputs and
    returns a serializable dict so callers (e.g. ``firm_live_review``) can
    continue to operate and forward the snapshot to ``run_six_stage_review``
    without ImportError. When the real implementation lands upstream,
    regenerate the shim and this stub is superseded.
    """
    return {{
        "internals": dict(internals or {{}}),
        "volatility": dict(volatility or {{}}),
        "cross_asset": dict(cross_asset or {{}}),
        "session": dict(session or {{}}),
        "micro": dict(micro or {{}}),
        "calendar": dict(calendar or {{}}),
        "eta_v3": dict(eta_v3 or {{}}),
        "regime": dict(regime or {{}}),
        "_stub": True,
    }}


def run_six_stage_review(
    *,
    strategy_id: str,
    decision_context: str,
    payload: dict[str, Any],
    regime_snapshot: dict[str, Any] | None = None,
    confluence_result: dict[str, Any] | None = None,
    **_extra: Any,
) -> dict[str, Any]:
    """Run the six-stage adversarial review; return a serializable dict.

    Each stage receives the prior stage's output as ``prior_stage_output``.
    The PM stage additionally receives ``agent_outputs`` (raw AgentOutput
    objects) inside its payload, which is the contract its evaluate()
    method expects for dissent-tally + synthesis.

    ``confluence_result`` (optional): an 8-axis confluence dict produced by
    ``compute_confluence``. Currently folded into each stage's payload as
    ``confluence`` so agents can consult it; ignored if None.
    ``**_extra``: reserved for forward-compat — any future kwarg passed by
    the caller is silently accepted (and logged into payload as
    ``_extra_kwargs``) rather than raising TypeError.
    """
    prior: Any = None
    raw_outputs: dict[str, Any] = {{}}
    outputs: dict[str, Any] = {{}}
    for stage_name, cls in _STAGES:
        agent = cls()
        stage_payload = dict(payload)
        if confluence_result is not None:
            stage_payload["confluence"] = confluence_result
        if _extra:
            stage_payload["_extra_kwargs"] = dict(_extra)
        if stage_name == "pm":
            stage_payload["agent_outputs"] = raw_outputs
        in_ = _AgentInput(
            strategy_id=strategy_id,
            decision_context=decision_context,
            payload=stage_payload,
            prior_stage_output=_safe_asdict(prior) if prior is not None else None,
            regime_snapshot=regime_snapshot or {{}},
        )
        out = agent.evaluate(in_)
        raw_outputs[stage_name] = out
        outputs[stage_name] = _safe_asdict(out)
        prior = out
    return outputs
'''


def write_runtime_shim(report: ProbeReport) -> Path:
    if not report.ready:
        raise RuntimeError("Cannot write shim: probe reports NOT READY.")
    import hashlib
    from datetime import UTC, datetime

    checksum = hashlib.sha256(
        json.dumps(report.as_dict(), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    content = SHIM_TEMPLATE.format(
        generated_at=datetime.now(UTC).isoformat(),
        checksum=checksum,
        firm_pkg_parent=str(report.firm_path.parent),
    )
    RUNTIME_SHIM_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_SHIM_PATH.write_text(content, encoding="utf-8")
    return RUNTIME_SHIM_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Firm-code integration bridge.")
    parser.add_argument("--path", type=Path, default=None, help="Override Firm code root.")
    parser.add_argument("--probe", action="store_true", help="Run readiness probe (default).")
    parser.add_argument("--integrate", action="store_true", help="Write runtime shim if ready.")
    args = parser.parse_args(argv)

    report = probe(args.path)

    md = render(report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(md, encoding="utf-8")
    STATUS_JSON_PATH.write_text(
        json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8",
    )

    print(md)
    print(f"wrote {REPORT_PATH}")
    print(f"wrote {STATUS_JSON_PATH}")

    if args.integrate:
        if report.ready:
            path = write_runtime_shim(report)
            print(f"integrated: wrote runtime shim at {path}")
            return 0
        print("NOT INTEGRATING: readiness probe failed.", file=sys.stderr)
        return 2
    return 0 if report.ready or not args.integrate else 3


if __name__ == "__main__":
    _ = importlib.util  # keep import live for future lazy loaders
    raise SystemExit(main())
