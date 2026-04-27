"""H4 closure: 9-gate live-promotion enforcement.

Per the Red Team review of v0.1.63 R1 (mnq_bot 2026-04-25, finding H4):

  > run_all_phases.py is non-blocking on every promotion gate. The
  > 9 gates HOLD claim from OPERATOR_BRIEFING is documentation;
  > the orchestrator does not actually fail on them. Operator
  > promotes a strategy because the orchestrator's "summary" line
  > says "70 ok / 9 fail / 79 total" -- and calling 70/79 a green
  > light is what gets a 50K eval blown.

This script encodes the 9 gates from
``docs/next_data_checkpoint.md`` and
``docs/OPERATOR_BRIEFING_2026_04_25.md`` as deterministic
pass/fail checks. Each gate reads an artifact (report file,
journal, manifest) and applies pass criteria.

The 9 gates
-----------
  1. walk_forward_ci_low      : WF fold-mean CI95 low > +0.05R
  2. block_bootstrap_ci_low   : Bootstrap CI95 low > +0.05R
  3. dsr_search               : DSR > 0.95 in search phase
  4. psr_deployment           : PSR vs zero > 0.95 in deployment
  5. n_trades_min             : pooled n_trades >= 200
  6. regime_stability         : >= 1 losing regime in cache
  7. dow_filter_placebo       : non-Thu < Thu by >= +0.05R
  8. knob_wf_sensitivity      : each knob at WF argmax
  9. paper_soak_min_weeks     : live $0-risk paper soak >= 2w

Usage
-----
    python scripts/_promotion_gate.py --gate walk_forward_ci_low
    python scripts/_promotion_gate.py --all
    python scripts/_promotion_gate.py --all --json

Exit codes (per gate)
---------------------
  0 -- PASS (gate criterion satisfied)
  1 -- FAIL (criterion violated; HOLD live promotion)
  2 -- NO_DATA (artifact missing; cannot evaluate; treat as HOLD)

Exit codes (--all)
------------------
  0 -- every gate PASS
  1 -- at least one gate FAIL
  2 -- at least one gate NO_DATA, no FAILs

Promotion policy
----------------
The promotion verdict is rc=0 ONLY when ALL 9 gates PASS. NO_DATA
counts as HOLD -- the operator must not promote until the artifact
exists and reports PASS.

Operator override
-----------------
The script has NO override. Failure is structural: the orchestrator
treats rc != 0 as "NOT promotion-eligible." Operator who wants to
ship anyway must edit ``eta_engine/config.json::execution.futures.mode``
manually with a deliberate operator action -- there is no flag here
that says "ignore the gates."
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Gate verdicts
PASS = 0
FAIL = 1
NO_DATA = 2

_VERDICT_NAME = {PASS: "PASS", FAIL: "FAIL", NO_DATA: "NO_DATA"}


@dataclass(frozen=True)
class GateResult:
    name: str
    verdict: int
    detail: str
    evidence: dict[str, Any]

    @property
    def verdict_name(self) -> str:
        return _VERDICT_NAME[self.verdict]


# ---------------------------------------------------------------------------
# Artifact readers (cheap, return None on missing-or-malformed)
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _journal_n_trades() -> int | None:
    """Count closed trades in the canonical live_sim journal."""
    try:
        from mnq.core.paths import LIVE_SIM_JOURNAL
        from mnq.storage.journal import EventJournal
        from mnq.storage.schema import FILL_REALIZED
    except ImportError:
        return None
    if not LIVE_SIM_JOURNAL.exists():
        return None
    try:
        j = EventJournal(LIVE_SIM_JOURNAL)
        n = sum(1 for _ in j.replay(event_types=(FILL_REALIZED,)))
        return n
    except Exception:  # noqa: BLE001 -- defensive; gate must never crash
        return None


# ---------------------------------------------------------------------------
# Individual gate evaluators
# ---------------------------------------------------------------------------


def _gate_walk_forward_ci_low() -> GateResult:
    """Walk-forward fold-mean CI95 low > +0.05R."""
    artifact = REPO_ROOT / "reports" / "walk_forward.json"
    data = _read_json(artifact)
    if data is None:
        return GateResult(
            "walk_forward_ci_low",
            NO_DATA,
            f"missing artifact: {artifact.relative_to(REPO_ROOT)}",
            {"artifact": str(artifact)},
        )
    ci_low = data.get("fold_mean_ci95_low")
    threshold = 0.05
    if ci_low is None:
        return GateResult(
            "walk_forward_ci_low",
            NO_DATA,
            "artifact present but missing 'fold_mean_ci95_low' field",
            {"artifact": str(artifact), "keys": list(data.keys())},
        )
    if ci_low > threshold:
        return GateResult(
            "walk_forward_ci_low",
            PASS,
            f"CI95 low {ci_low:+.3f}R > {threshold:+.3f}R threshold",
            {"ci_low": ci_low, "threshold": threshold},
        )
    return GateResult(
        "walk_forward_ci_low",
        FAIL,
        f"CI95 low {ci_low:+.3f}R does NOT exceed {threshold:+.3f}R threshold",
        {"ci_low": ci_low, "threshold": threshold},
    )


def _gate_block_bootstrap_ci_low() -> GateResult:
    """Block-bootstrap CI95 low > +0.05R."""
    artifact = REPO_ROOT / "reports" / "block_bootstrap.json"
    data = _read_json(artifact)
    if data is None:
        return GateResult(
            "block_bootstrap_ci_low",
            NO_DATA,
            f"missing artifact: {artifact.relative_to(REPO_ROOT)}",
            {"artifact": str(artifact)},
        )
    ci_low = data.get("ci95_low")
    threshold = 0.05
    if ci_low is None:
        return GateResult(
            "block_bootstrap_ci_low",
            NO_DATA,
            "artifact present but missing 'ci95_low' field",
            {"artifact": str(artifact), "keys": list(data.keys())},
        )
    if ci_low > threshold:
        return GateResult(
            "block_bootstrap_ci_low",
            PASS,
            f"bootstrap CI95 low {ci_low:+.3f}R > {threshold:+.3f}R",
            {"ci_low": ci_low, "threshold": threshold},
        )
    return GateResult(
        "block_bootstrap_ci_low",
        FAIL,
        f"bootstrap CI95 low {ci_low:+.3f}R does NOT exceed {threshold:+.3f}R",
        {"ci_low": ci_low, "threshold": threshold},
    )


def _gate_dsr_search() -> GateResult:
    """DSR > 0.95 in search phase."""
    artifact = REPO_ROOT / "reports" / "dsr.json"
    data = _read_json(artifact)
    if data is None:
        return GateResult(
            "dsr_search",
            NO_DATA,
            f"missing artifact: {artifact.relative_to(REPO_ROOT)}",
            {"artifact": str(artifact)},
        )
    dsr = data.get("dsr_search")
    threshold = 0.95
    if dsr is None:
        return GateResult(
            "dsr_search",
            NO_DATA,
            "artifact missing 'dsr_search' field",
            {"artifact": str(artifact), "keys": list(data.keys())},
        )
    if dsr > threshold:
        return GateResult(
            "dsr_search",
            PASS,
            f"DSR(search) {dsr:.4f} > {threshold:.4f}",
            {"dsr": dsr, "threshold": threshold},
        )
    return GateResult(
        "dsr_search",
        FAIL,
        f"DSR(search) {dsr:.4f} does NOT exceed {threshold:.4f}",
        {"dsr": dsr, "threshold": threshold},
    )


def _gate_psr_deployment() -> GateResult:
    """PSR vs zero > 0.95 in deployment phase."""
    artifact = REPO_ROOT / "reports" / "psr.json"
    data = _read_json(artifact)
    if data is None:
        return GateResult(
            "psr_deployment",
            NO_DATA,
            f"missing artifact: {artifact.relative_to(REPO_ROOT)}",
            {"artifact": str(artifact)},
        )
    psr = data.get("psr_deployment")
    threshold = 0.95
    if psr is None:
        return GateResult(
            "psr_deployment",
            NO_DATA,
            "artifact missing 'psr_deployment' field",
            {"artifact": str(artifact), "keys": list(data.keys())},
        )
    if psr > threshold:
        return GateResult(
            "psr_deployment",
            PASS,
            f"PSR(deployment) {psr:.4f} > {threshold:.4f}",
            {"psr": psr, "threshold": threshold},
        )
    return GateResult(
        "psr_deployment",
        FAIL,
        f"PSR(deployment) {psr:.4f} does NOT exceed {threshold:.4f}",
        {"psr": psr, "threshold": threshold},
    )


def _gate_n_trades_min() -> GateResult:
    """Pooled n_trades >= 200."""
    n = _journal_n_trades()
    threshold = 200
    if n is None:
        return GateResult(
            "n_trades_min",
            NO_DATA,
            "could not read live_sim journal trade count",
            {},
        )
    if n >= threshold:
        return GateResult(
            "n_trades_min",
            PASS,
            f"n_trades = {n} >= {threshold}",
            {"n_trades": n, "threshold": threshold},
        )
    return GateResult(
        "n_trades_min",
        FAIL,
        f"n_trades = {n} < {threshold} (need {threshold - n} more)",
        {"n_trades": n, "threshold": threshold},
    )


def _gate_regime_stability() -> GateResult:
    """At least 1 losing regime in the cache."""
    artifact = REPO_ROOT / "reports" / "regime_classification.json"
    data = _read_json(artifact)
    if data is None:
        return GateResult(
            "regime_stability",
            NO_DATA,
            f"missing artifact: {artifact.relative_to(REPO_ROOT)}",
            {"artifact": str(artifact)},
        )
    losing_regimes = data.get("losing_regimes", [])
    if isinstance(losing_regimes, list) and len(losing_regimes) >= 1:
        return GateResult(
            "regime_stability",
            PASS,
            f"{len(losing_regimes)} losing regime(s) observed",
            {"losing_regimes": losing_regimes},
        )
    return GateResult(
        "regime_stability",
        FAIL,
        "no losing regime observed; strategy hasn't seen its own "
        "failure mode -- live promotion is regime-cherrypicked.",
        {"losing_regimes": losing_regimes},
    )


def _gate_dow_filter_placebo() -> GateResult:
    """DOW filter placebo: non-Thu placebos < Thu by >= +0.05R."""
    artifact = REPO_ROOT / "reports" / "dow_placebo.json"
    data = _read_json(artifact)
    if data is None:
        return GateResult(
            "dow_filter_placebo",
            NO_DATA,
            f"missing artifact: {artifact.relative_to(REPO_ROOT)}",
            {"artifact": str(artifact)},
        )
    margin = data.get("thu_vs_others_margin_r")
    threshold = 0.05
    if margin is None:
        return GateResult(
            "dow_filter_placebo",
            NO_DATA,
            "artifact missing 'thu_vs_others_margin_r' field",
            {"artifact": str(artifact), "keys": list(data.keys())},
        )
    if margin >= threshold:
        return GateResult(
            "dow_filter_placebo",
            PASS,
            f"Thu margin over non-Thu = {margin:+.3f}R >= {threshold:+.3f}R",
            {"margin_r": margin, "threshold": threshold},
        )
    return GateResult(
        "dow_filter_placebo",
        FAIL,
        f"Thu margin over non-Thu = {margin:+.3f}R < {threshold:+.3f}R "
        "(filter is not real -- DOW edge could be noise)",
        {"margin_r": margin, "threshold": threshold},
    )


def _gate_knob_wf_sensitivity() -> GateResult:
    """Each knob at WF argmax."""
    artifact = REPO_ROOT / "reports" / "knob_wf_sensitivity.json"
    data = _read_json(artifact)
    if data is None:
        return GateResult(
            "knob_wf_sensitivity",
            NO_DATA,
            f"missing artifact: {artifact.relative_to(REPO_ROOT)}",
            {"artifact": str(artifact)},
        )
    knobs = data.get("knobs") or []
    if not isinstance(knobs, list) or not knobs:
        return GateResult(
            "knob_wf_sensitivity",
            NO_DATA,
            "artifact missing 'knobs' list",
            {"artifact": str(artifact), "keys": list(data.keys())},
        )
    bad = [k for k in knobs if not k.get("at_argmax", False)]
    if not bad:
        return GateResult(
            "knob_wf_sensitivity",
            PASS,
            f"all {len(knobs)} knobs at walk-forward argmax",
            {"knobs": knobs},
        )
    return GateResult(
        "knob_wf_sensitivity",
        FAIL,
        f"{len(bad)} knob(s) not at WF argmax: {', '.join(k.get('name', '?') for k in bad)}",
        {"knobs": knobs, "off_argmax": bad},
    )


def _gate_paper_soak_min_weeks() -> GateResult:
    """Live $0-risk paper soak >= 2 weeks."""
    artifact = REPO_ROOT / "reports" / "paper_soak.json"
    data = _read_json(artifact)
    if data is None:
        return GateResult(
            "paper_soak_min_weeks",
            NO_DATA,
            f"missing artifact: {artifact.relative_to(REPO_ROOT)}",
            {"artifact": str(artifact)},
        )
    weeks = data.get("weeks_clean")
    threshold = 2
    if weeks is None:
        return GateResult(
            "paper_soak_min_weeks",
            NO_DATA,
            "artifact missing 'weeks_clean' field",
            {"artifact": str(artifact), "keys": list(data.keys())},
        )
    if weeks >= threshold:
        return GateResult(
            "paper_soak_min_weeks",
            PASS,
            f"{weeks:.1f} weeks of clean paper >= {threshold}",
            {"weeks": weeks, "threshold": threshold},
        )
    return GateResult(
        "paper_soak_min_weeks",
        FAIL,
        f"only {weeks:.1f} weeks of clean paper (need {threshold})",
        {"weeks": weeks, "threshold": threshold},
    )


# Ordered registry. Each entry is (gate_name, evaluator).
_GATES = [
    ("walk_forward_ci_low", _gate_walk_forward_ci_low),
    ("block_bootstrap_ci_low", _gate_block_bootstrap_ci_low),
    ("dsr_search", _gate_dsr_search),
    ("psr_deployment", _gate_psr_deployment),
    ("n_trades_min", _gate_n_trades_min),
    ("regime_stability", _gate_regime_stability),
    ("dow_filter_placebo", _gate_dow_filter_placebo),
    ("knob_wf_sensitivity", _gate_knob_wf_sensitivity),
    ("paper_soak_min_weeks", _gate_paper_soak_min_weeks),
]
_GATE_NAMES = [name for name, _ in _GATES]


def evaluate(name: str) -> GateResult:
    for gate_name, fn in _GATES:
        if gate_name == name:
            return fn()
    msg = f"unknown gate: {name!r}. Known gates: {', '.join(_GATE_NAMES)}"
    raise ValueError(msg)


def evaluate_all() -> list[GateResult]:
    return [fn() for _, fn in _GATES]


def aggregate_verdict(results: list[GateResult]) -> int:
    if any(r.verdict == FAIL for r in results):
        return 1
    if any(r.verdict == NO_DATA for r in results):
        return 2
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_human(result: GateResult) -> None:
    icon = {"PASS": "[+]", "FAIL": "[-]", "NO_DATA": "[?]"}[result.verdict_name]
    print(f"  {icon} {result.name:<28s} {result.verdict_name:<8s} {result.detail}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--gate",
        choices=_GATE_NAMES,
        help="evaluate a single gate; exit code = its verdict",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="evaluate all 9 gates; exit code = aggregate",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of human report",
    )
    args = p.parse_args(argv)

    if args.gate:
        result = evaluate(args.gate)
        if args.json:
            print(
                json.dumps(
                    {
                        "gate": result.name,
                        "verdict": result.verdict_name,
                        "detail": result.detail,
                        "evidence": result.evidence,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Gate: {result.name}")
            _print_human(result)
        return result.verdict

    # --all
    results = evaluate_all()
    rc = aggregate_verdict(results)
    if args.json:
        print(
            json.dumps(
                {
                    "rc": rc,
                    "verdict": _VERDICT_NAME[rc] if rc != 1 else "FAIL",
                    "n_pass": sum(1 for r in results if r.verdict == PASS),
                    "n_fail": sum(1 for r in results if r.verdict == FAIL),
                    "n_no_data": sum(1 for r in results if r.verdict == NO_DATA),
                    "gates": [
                        {
                            "name": r.name,
                            "verdict": r.verdict_name,
                            "detail": r.detail,
                            "evidence": r.evidence,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
    else:
        print("PROMOTION-GATE EVALUATION")
        print("=" * 64)
        for r in results:
            _print_human(r)
        print("=" * 64)
        n_pass = sum(1 for r in results if r.verdict == PASS)
        n_fail = sum(1 for r in results if r.verdict == FAIL)
        n_no_data = sum(1 for r in results if r.verdict == NO_DATA)
        print(
            f"Verdict: {_VERDICT_NAME[rc] if rc != 1 else 'FAIL'} "
            f"({n_pass} PASS, {n_fail} FAIL, {n_no_data} NO_DATA)",
        )
        if rc != 0:
            print(
                "\nLIVE PROMOTION HOLD. The promotion verdict is rc=0 "
                "ONLY when ALL 9 gates PASS. Gates with verdict NO_DATA "
                "count as HOLD until the underlying artifact ships.",
            )
    return rc


if __name__ == "__main__":
    sys.exit(main())
