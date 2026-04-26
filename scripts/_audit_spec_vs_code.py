"""H1 closure (Red Team review 2026-04-25): spec-vs-code reconciler.

The Red Team review observed:

  > The "specs → code generation" pipeline only knows about
  > `mnq_baseline_v0_1.yaml`. The variants the live path cares about
  > (r5_real_wide_target etc.) live in `scripts/strategy_v2.py:323-378`
  > as Python literals, never went through codegen, and have no spec
  > hash for parent-vs-child detection.

This script walks `scripts/strategy_v2.py` for every ``StrategyConfig(
name="...")`` literal, then asserts that ``specs/strategies/`` contains
either a YAML named ``<name>.yaml`` or a baseline that lists ``<name>``
as a known variant.

Today this is **advisory** -- it surfaces variants that live as Python
literals without spec backing, lets the operator review the list, and
exits 0 unless ``--strict`` is passed. Promoting to gating is a
follow-up decision once the operator decides whether spec-backing is
required for every variant or only for the live-promoted ones.

Pattern-matched against `eta_engine/scripts/_audit_roadmap_vs_code.py`.

Usage
-----
    python scripts/_audit_spec_vs_code.py
    python scripts/_audit_spec_vs_code.py --strict   # exit 1 on any unbacked variant
    python scripts/_audit_spec_vs_code.py --json     # machine output

Exit codes
----------
0 -- ran successfully (advisory)
1 -- ``--strict`` and at least one variant lacks a spec
2 -- could not parse strategy_v2.py / specs/ dir missing
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = ROOT / "scripts" / "strategy_v2.py"
SPECS_DIR = ROOT / "specs" / "strategies"


@dataclass(frozen=True)
class VariantHit:
    """One ``StrategyConfig(name=...)`` literal site."""

    name: str
    line: int
    has_spec: bool
    spec_evidence: str | None


def _walk_strategy_configs(text: str) -> list[tuple[str, int]]:
    """Walk strategy_v2.py AST for ``StrategyConfig(name=<literal>, ...)``.

    Returns a list of (name, lineno) for every literal site.
    """
    tree = ast.parse(text)
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match ``StrategyConfig(...)`` -- bare callable
        if not (
            isinstance(node.func, ast.Name)
            and node.func.id == "StrategyConfig"
        ):
            continue
        # Find name= keyword
        for kw in node.keywords:
            if kw.arg != "name":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(
                kw.value.value, str,
            ):
                hits.append((kw.value.value, node.lineno))
            break
    return hits


def _spec_evidence_for(name: str) -> str | None:
    """Return the path-or-baseline-key that backs ``name``, or None."""
    # 1) Direct file match
    direct = SPECS_DIR / f"{name}.yaml"
    if direct.exists():
        return str(direct.relative_to(ROOT))
    # 2) Baseline lists it as a known variant (look in baseline metadata)
    baseline = SPECS_DIR / "v0_1_baseline.yaml"
    if not baseline.exists():
        return None
    try:
        data = yaml.safe_load(baseline.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    # Probe a few common shapes for variant lists
    for key in ("variants", "known_variants", "registered_variants"):
        section = data.get(key)
        if isinstance(section, list) and name in section:
            return f"specs/strategies/v0_1_baseline.yaml::{key}"
        if isinstance(section, dict) and name in section:
            return f"specs/strategies/v0_1_baseline.yaml::{key}.{name}"
    return None


def scan() -> list[VariantHit]:
    if not STRATEGY_FILE.exists():
        return []
    text = STRATEGY_FILE.read_text(encoding="utf-8")
    raw_hits = _walk_strategy_configs(text)
    out: list[VariantHit] = []
    seen: set[str] = set()
    for name, line in raw_hits:
        if name in seen:
            # Same variant declared twice (live-sim + tests) -- only
            # report once at the first site.
            continue
        seen.add(name)
        evidence = _spec_evidence_for(name)
        out.append(VariantHit(
            name=name,
            line=line,
            has_spec=evidence is not None,
            spec_evidence=evidence,
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any variant lacks spec backing",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )
    args = p.parse_args(argv)

    if not STRATEGY_FILE.exists():
        print(f"ERROR: {STRATEGY_FILE} not found", file=sys.stderr)
        return 2
    if not SPECS_DIR.exists():
        print(f"ERROR: {SPECS_DIR} not found", file=sys.stderr)
        return 2

    hits = scan()
    backed = [h for h in hits if h.has_spec]
    unbacked = [h for h in hits if not h.has_spec]

    if args.json:
        print(json.dumps({
            "total": len(hits),
            "backed": len(backed),
            "unbacked": len(unbacked),
            "hits": [
                {
                    "name": h.name,
                    "line": h.line,
                    "has_spec": h.has_spec,
                    "spec_evidence": h.spec_evidence,
                }
                for h in hits
            ],
        }, indent=2))
    else:
        print("SPEC-VS-CODE AUDIT (mnq_bot)")
        print("=" * 50)
        print(
            f"Variants found: {len(hits)}  spec-backed: {len(backed)}  "
            f"unbacked: {len(unbacked)}",
        )
        print()
        if backed:
            print("BACKED (variant has a spec):")
            for h in backed:
                print(f"  {h.name:<30s}  ->  {h.spec_evidence}")
            print()
        if unbacked:
            print("UNBACKED (StrategyConfig literal without spec entry):")
            for h in unbacked:
                print(f"  {h.name:<30s}  scripts/strategy_v2.py:{h.line}")
            print()
            print(
                "Each unbacked variant should either grow an entry in "
                "specs/strategies/<name>.yaml OR a registered_variants "
                "entry under specs/strategies/v0_1_baseline.yaml. Until "
                "then the parent_hash chain that detects "
                "config-vs-code drift is broken for these variants."
            )
            print()
            print(
                f"FAIL -- {len(unbacked)} StrategyConfig variant(s) "
                f"in scripts/strategy_v2.py have no spec backing.",
            )
        else:
            print("OK -- every operationally-active StrategyConfig has spec backing.")

    if args.strict and unbacked:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
