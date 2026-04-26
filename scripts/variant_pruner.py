"""Variant pruner -- classify each variant in strategy_v2.VARIANTS as
PRUNE / WATCH / KEEP based on its regime-expectancy evidence (v0.2.15).

The promotion-pipeline's gate set already keeps un-validated variants
out of LIVE. This script answers a different question: of the 49+
StrategyConfig variants in scripts/strategy_v2.py, which ones deserve
to STAY around for further iteration vs. which ones should be
deleted to keep the search space lean?

Classification rules
--------------------
Each variant is bucketed by inspecting its v0.2.13 spec_payload:

  * PRUNE -- provenance is exactly ["stub"] (no calibration source
    available at all) OR no regime has both expectancy_r > +0.05R
    AND n_days >= 1.
    => Variant has zero positive evidence; deleting it costs nothing.

  * WATCH -- at least one regime has expectancy_r > +0.05R but every
    such regime has n_days < 5 (thin sample).
    => Variant looks promising but the evidence is too thin to
    promote. Run it longer before judging.

  * KEEP -- at least one regime has expectancy_r > +0.05R AND
    n_days >= 5 (thick sample, real edge).
    => Variant has demonstrated calibrated edge. Keep it.

The thresholds match the v0.2.14 ``regime_report.py`` summary's
"real edge + thick evidence" definition so the two reports stay
consistent.

This script does NOT delete variants from strategy_v2.py automatically.
Auto-deletion of source code is risky (dependencies, fixtures, etc.).
The script just produces a recommended PRUNE list; the operator does
the deletion in a reviewable PR.

Usage
-----
    python scripts/variant_pruner.py
    python scripts/variant_pruner.py --output reports/prune_list.md
    python scripts/variant_pruner.py --json
    python scripts/variant_pruner.py --bucket PRUNE   # stdout list

Exit code is always 0 (this is a reporter, not a gate).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mnq.spec.runtime_payload import build_spec_payload  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "reports" / "variant_prune_list.md"

# Edge / evidence thresholds. Match the v0.2.14 regime_report.py
# "real edge + thick evidence" criterion so the bucketing stays
# consistent across reports.
EDGE_R_THRESHOLD = 0.05
THICK_SAMPLE_MIN_DAYS = 5

Bucket = str  # "PRUNE" | "WATCH" | "KEEP"
PRUNE = "PRUNE"
WATCH = "WATCH"
KEEP = "KEEP"


def classify_variant(payload: dict[str, Any]) -> tuple[Bucket, str]:
    """Bucket one variant + return a one-line reason.

    Bucketing is deterministic given the same payload.
    """
    provenance = list(payload.get("provenance") or [])
    regime_exp: dict[str, dict[str, float]] = (
        payload.get("regime_expectancy") or {}
    )

    if provenance == ["stub"]:
        return PRUNE, "stub provenance -- no calibration source"

    # Find regimes with expectancy_r > threshold
    promising = {
        regime: stats
        for regime, stats in regime_exp.items()
        if stats.get("expectancy_r", 0.0) > EDGE_R_THRESHOLD
    }
    if not promising:
        return (
            PRUNE,
            f"no regime with expectancy_r > {EDGE_R_THRESHOLD:+.3f}R",
        )

    # Among the promising, any with thick sample?
    thick = {
        regime: stats
        for regime, stats in promising.items()
        if stats.get("n_days", 0.0) >= THICK_SAMPLE_MIN_DAYS
    }
    if thick:
        # Pick the regime with the highest expectancy_r for the reason
        best = max(thick.items(), key=lambda kv: kv[1].get("expectancy_r", 0.0))
        return (
            KEEP,
            f"edge in {best[0]}: "
            f"E={best[1]['expectancy_r']:+.3f}R over n={int(best[1]['n_days'])} days",
        )

    # All promising regimes are thin
    best_thin = max(
        promising.items(), key=lambda kv: kv[1].get("expectancy_r", 0.0),
    )
    return (
        WATCH,
        f"thin sample in {best_thin[0]}: "
        f"E={best_thin[1]['expectancy_r']:+.3f}R over only "
        f"n={int(best_thin[1]['n_days'])} days",
    )


def _build_classified() -> list[dict[str, Any]]:
    """Iterate variants, build payload, classify. Returns list of rows."""
    try:
        from strategy_v2 import VARIANTS  # type: ignore
    except ImportError:
        return []
    rows: list[dict[str, Any]] = []
    for cfg in VARIANTS:
        payload = build_spec_payload(cfg.name)
        bucket, reason = classify_variant(payload)
        rows.append({
            "variant": cfg.name,
            "bucket": bucket,
            "reason": reason,
            "provenance": payload.get("provenance", ["stub"]),
            "n_total": payload.get("sample_size", 0),
            "expected_expectancy_r": payload.get("expected_expectancy_r", 0.0),
        })
    return rows


def _render_markdown(rows: list[dict[str, Any]]) -> str:
    """Render a 3-section markdown report (PRUNE / WATCH / KEEP)."""
    by_bucket: dict[str, list[dict[str, Any]]] = {
        PRUNE: [], WATCH: [], KEEP: [],
    }
    for row in rows:
        by_bucket[row["bucket"]].append(row)

    lines: list[str] = [
        "# Variant pruner",
        "",
        "Generated by `scripts/variant_pruner.py`. Each variant is ",
        "bucketed by its v0.2.13 regime-expectancy evidence:",
        "",
        f"  * **PRUNE** -- no regime with expectancy_r > {EDGE_R_THRESHOLD:+.3f}R, "
        "or stub-only provenance",
        f"  * **WATCH** -- has edge but thinnest regime n_days < {THICK_SAMPLE_MIN_DAYS}",
        f"  * **KEEP**  -- has edge AND a regime with n_days >= {THICK_SAMPLE_MIN_DAYS}",
        "",
        "Operator action: review the **PRUNE** list and delete those ",
        "variants from `scripts/strategy_v2.py::VARIANTS` in a ",
        "reviewable PR. Auto-deletion is intentionally NOT done here.",
        "",
    ]
    lines.append("## Summary")
    lines.append("")
    lines.append(f"* Total variants: **{len(rows)}**")
    lines.append(f"* PRUNE: **{len(by_bucket[PRUNE])}**")
    lines.append(f"* WATCH: **{len(by_bucket[WATCH])}**")
    lines.append(f"* KEEP:  **{len(by_bucket[KEEP])}**")
    lines.append("")
    for bucket_name in (PRUNE, WATCH, KEEP):
        bucket_rows = by_bucket[bucket_name]
        lines.append(f"## {bucket_name}  ({len(bucket_rows)} variant(s))")
        lines.append("")
        if not bucket_rows:
            lines.append("_(none)_")
            lines.append("")
            continue
        lines.append("| variant | reason | provenance |")
        lines.append("|---|---|---|")
        for row in sorted(bucket_rows, key=lambda r: r["variant"]):
            lines.append(
                f"| {row['variant']} | {row['reason']} | "
                f"{','.join(row['provenance'])} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"output markdown file (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of markdown (machine-readable)",
    )
    p.add_argument(
        "--bucket", choices=[PRUNE, WATCH, KEEP], default=None,
        help="print just one bucket's variant names to stdout (one per line)",
    )
    args = p.parse_args(argv)

    rows = _build_classified()
    if not rows:
        print("no variants resolved (strategy_v2.VARIANTS empty?)", file=sys.stderr)
        return 0

    if args.bucket:
        for row in sorted(rows, key=lambda r: r["variant"]):
            if row["bucket"] == args.bucket:
                print(row["variant"])
        return 0

    if args.json:
        n = {b: sum(1 for r in rows if r["bucket"] == b) for b in (PRUNE, WATCH, KEEP)}
        print(json.dumps(
            {"variants": rows, "summary": {"total": len(rows), **n}},
            indent=2, default=str,
        ))
        return 0

    md = _render_markdown(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    n_prune = sum(1 for r in rows if r["bucket"] == PRUNE)
    n_watch = sum(1 for r in rows if r["bucket"] == WATCH)
    n_keep = sum(1 for r in rows if r["bucket"] == KEEP)
    print(f"wrote {args.output} ({len(rows)} variants)")
    print(f"summary: PRUNE={n_prune} WATCH={n_watch} KEEP={n_keep}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
