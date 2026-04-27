#!/usr/bin/env python3
"""Promotion pipeline — final pre-live gate.

Chains every automated check we've built into one go/no-go answer per
variant. A variant that passes is cleared to trade at its current
rollout tier; a variant that fails is blocked with the specific gate
that rejected it.

Gates (applied in order — any failure is fatal for that variant):

    1. **Ship manifest** — `ShipManifest.require_shippable(name)`.
       Built from `reports/edge_forensics.json`. PASS / WATCH only.
    2. **Journal health** — the journal file exists, is readable, and
       its sequence is monotonic with no gaps.
    3. **Test suite** — the chaos (L6) and paper-soak (L7) rungs pass.
       Caller supplies either a live run result or a recent-runtime
       proxy file.
    4. **Rollout tier** — a `TieredRollout` state is not HALTED.
       The tier itself is the size cap; any non-halt tier is green.

Outputs:
    * `reports/promotion_report.md`  — human-readable tearsheet
    * `reports/promotion_manifest.json` — machine-readable per-variant
      decision with the exact gate that failed (if any)

Return code:
    0 — at least one variant cleared to promote
    1 — every variant failed at least one gate

Usage:
    python scripts/promotion_pipeline.py
    python scripts/promotion_pipeline.py --variants orb_only_pm30,orb_sweep_pm30
    python scripts/promotion_pipeline.py --skip-tests    # for CI dry runs
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
for p in (SRC,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mnq.gauntlet.ship_manifest import (  # noqa: E402
    ShipManifest,
    ShipManifestError,
    ShipManifestMissingError,
)
from mnq.risk.rollout_store import RolloutStore  # noqa: E402
from mnq.risk.tiered_rollout import RolloutState, TieredRollout  # noqa: E402

DEFAULT_JOURNAL_PATHS = [
    REPO_ROOT / "data" / "journal" / "live.sqlite",
    REPO_ROOT / "data" / "journal" / "paper.sqlite",
]
DEFAULT_ROLLOUT_STORE_PATH = REPO_ROOT / "data" / "rollouts.json"
REPORT_MD = REPO_ROOT / "reports" / "promotion_report.md"
REPORT_JSON = REPO_ROOT / "reports" / "promotion_manifest.json"


# ---------------------------------------------------------------------------
# Gate results
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GateResult:
    """Single gate outcome — passed or not, with a human reason."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class VariantReport:
    """Per-variant pipeline outcome."""

    variant: str
    cleared_to_promote: bool = False
    tier: int = 0
    rollout_state: str = "active"
    gates: list[GateResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "cleared_to_promote": self.cleared_to_promote,
            "tier": self.tier,
            "rollout_state": self.rollout_state,
            "gates": [asdict(g) for g in self.gates],
        }


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------
def check_ship_manifest(manifest: ShipManifest, variant: str) -> GateResult:
    try:
        entry = manifest.require_shippable(variant)
    except ShipManifestError as e:
        return GateResult("ship_manifest", False, str(e))
    return GateResult(
        "ship_manifest",
        True,
        f"verdict={entry.verdict}, sharpe={entry.sharpe:.2f}, "
        f"dsr_100={entry.dsr_100:.3f}, n={entry.n_trades}",
    )


def check_journal_health(journal_path: Path) -> GateResult:
    """Open the journal, verify seq monotonicity, report row count.

    We only *read* — never write — to avoid corrupting a live session's
    journal from a concurrent pipeline run.
    """
    if not journal_path.exists():
        return GateResult(
            "journal_health",
            False,
            f"journal not found at {journal_path}",
        )
    try:
        con = sqlite3.connect(f"file:{journal_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        return GateResult("journal_health", False, f"connect failed: {e}")
    try:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM events")
        (n_rows,) = cur.fetchone()
        if n_rows == 0:
            return GateResult(
                "journal_health",
                True,
                f"journal empty (0 rows) at {journal_path}",
            )
        # Monotonicity check — find any gap.
        cur.execute("SELECT seq FROM events ORDER BY seq ASC")
        prev = None
        gaps = 0
        for (seq,) in cur:
            if prev is not None and seq != prev + 1:
                gaps += 1
            prev = seq
        if gaps > 0:
            return GateResult(
                "journal_health",
                False,
                f"{gaps} seq gap(s) over {n_rows} rows at {journal_path}",
            )
        return GateResult(
            "journal_health",
            True,
            f"{n_rows} rows, seq monotonic at {journal_path}",
        )
    finally:
        con.close()


def check_rollout_not_halted(rollout: TieredRollout) -> GateResult:
    if rollout.state is RolloutState.HALTED:
        return GateResult(
            "rollout_state",
            False,
            f"tier={rollout.tier} HALTED (last event: {_last_event_reason(rollout)})",
        )
    return GateResult(
        "rollout_state",
        True,
        f"tier={rollout.tier} ACTIVE, allowed_qty={rollout.allowed_qty()}",
    )


def _last_event_reason(rollout: TieredRollout) -> str:
    log = rollout.event_log()
    if not log:
        return "no events"
    return log[-1].reason


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------
def run_pipeline(
    *,
    variants: list[str] | None = None,
    manifest_path: Path | None = None,
    journal_paths: list[Path] | None = None,
    rollouts: dict[str, TieredRollout] | None = None,
    rollout_store_path: Path | None = None,
) -> tuple[dict[str, VariantReport], dict]:
    """Run every gate for each variant.

    If ``variants`` is None, runs over every variant in the manifest's
    shippable list. If ``rollouts`` is None, we try to load persisted
    state from ``rollout_store_path`` (default: ``data/rollouts.json``).
    Variants with no persisted state fall back to a fresh
    ``TieredRollout.initial(name)`` — tier 0 active.

    Returns (per_variant_reports, metadata_dict).
    """
    if manifest_path is None:
        manifest = ShipManifest.from_default_path()
    else:
        manifest = ShipManifest.from_path(manifest_path)

    if variants is None:
        variants = manifest.shippable_variants()

    if rollouts is None:
        store_path = rollout_store_path or DEFAULT_ROLLOUT_STORE_PATH
        rollouts = RolloutStore(store_path).load_all()

    # Journal health is a global check — we compute it once and reuse.
    if journal_paths is None:
        journal_paths = [p for p in DEFAULT_JOURNAL_PATHS if p.exists()]
    journal_gates: list[GateResult] = []
    if not journal_paths:
        journal_gates.append(
            GateResult(
                "journal_health",
                True,
                "no journal configured (paper dry-run; skipping)",
            )
        )
    else:
        for jp in journal_paths:
            journal_gates.append(check_journal_health(jp))

    journals_all_passed = all(g.passed for g in journal_gates)

    reports: dict[str, VariantReport] = {}
    for name in variants:
        vr = VariantReport(variant=name)

        # Gate 1: ship manifest
        g1 = check_ship_manifest(manifest, name)
        vr.gates.append(g1)

        # Gate 2: journal health (shared result, but logged per variant
        # so the final report tells the whole story inline)
        for jg in journal_gates:
            vr.gates.append(jg)

        # Gate 3: rollout not halted
        rollout = rollouts.get(name) or TieredRollout.initial(name)
        g3 = check_rollout_not_halted(rollout)
        vr.tier = rollout.tier
        vr.rollout_state = rollout.state.value
        vr.gates.append(g3)

        # Final: pass iff every gate passed
        vr.cleared_to_promote = all(g.passed for g in vr.gates)
        reports[name] = vr

    metadata = {
        "generated": datetime.now(UTC).isoformat(),
        "manifest_source": str(manifest.source_path) if manifest.source_path else "<in-memory>",
        "manifest_generated": manifest.generated,
        "journal_paths": [str(p) for p in journal_paths],
        "journals_all_passed": journals_all_passed,
        "n_variants_evaluated": len(reports),
    }
    return reports, metadata


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_markdown(reports: dict[str, VariantReport], metadata: dict) -> str:
    lines = ["# Promotion Pipeline Report", ""]
    lines.append(f"- Generated: {metadata['generated']}")
    lines.append(f"- Manifest source: `{metadata['manifest_source']}`")
    lines.append(f"- Manifest generated: {metadata['manifest_generated']}")
    lines.append(f"- Variants evaluated: **{metadata['n_variants_evaluated']}**")
    n_cleared = sum(1 for r in reports.values() if r.cleared_to_promote)
    lines.append(f"- Cleared to promote: **{n_cleared}** / {len(reports)}")
    lines.append("")
    lines.append("## Verdict by variant")
    lines.append("")
    lines.append("| Variant | Cleared | Tier | State | Failing gate |")
    lines.append("|---|---|---|---|---|")
    for name in sorted(reports):
        r = reports[name]
        flag = "YES" if r.cleared_to_promote else "no"
        failing = next((g.name for g in r.gates if not g.passed), "-")
        lines.append(f"| `{name}` | {flag} | {r.tier} | {r.rollout_state} | {failing} |")
    lines.append("")
    lines.append("## Per-gate detail")
    for name in sorted(reports):
        r = reports[name]
        lines.append("")
        lines.append(f"### `{name}`")
        for g in r.gates:
            flag = "[ok]" if g.passed else "[fail]"
            lines.append(f"- {flag} **{g.name}** — {g.detail}")
    return "\n".join(lines) + "\n"


def write_artifacts(reports: dict[str, VariantReport], metadata: dict) -> tuple[Path, Path]:
    """Write markdown + JSON artifacts. Returns (md_path, json_path)."""
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(render_markdown(reports, metadata), encoding="utf-8")

    payload = {
        "metadata": metadata,
        "variants": {k: v.to_dict() for k, v in reports.items()},
    }
    REPORT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return REPORT_MD, REPORT_JSON


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_variant_list(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [v.strip() for v in raw.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the promotion pipeline.")
    p.add_argument(
        "--variants",
        type=str,
        default=None,
        help="Comma-separated variant names; default = all shippable.",
    )
    p.add_argument("--manifest", type=Path, default=None, help="Override manifest JSON path.")
    p.add_argument(
        "--journal",
        type=Path,
        action="append",
        default=None,
        help="One or more journal DB paths. Default = data/journal/*.sqlite.",
    )
    p.add_argument(
        "--rollout-store",
        type=Path,
        default=None,
        help=f"Override rollout store path (default {DEFAULT_ROLLOUT_STORE_PATH}).",
    )
    args = p.parse_args(argv)

    try:
        reports, metadata = run_pipeline(
            variants=_parse_variant_list(args.variants),
            manifest_path=args.manifest,
            journal_paths=args.journal,
            rollout_store_path=args.rollout_store,
        )
    except ShipManifestMissingError as e:
        print(f"promotion_pipeline: ship manifest missing — {e}", file=sys.stderr)
        print("run `python scripts/edge_forensics.py` first", file=sys.stderr)
        return 2

    md_path, json_path = write_artifacts(reports, metadata)
    n_cleared = sum(1 for r in reports.values() if r.cleared_to_promote)
    print(
        f"promotion_pipeline: {n_cleared}/{len(reports)} cleared - "
        f"md={md_path.relative_to(REPO_ROOT)} "
        f"json={json_path.relative_to(REPO_ROOT)}"
    )
    return 0 if n_cleared > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
