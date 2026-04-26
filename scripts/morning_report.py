"""Morning report -- consolidated operator status (v0.2.20).

One script the operator runs once a day (or before a paper-soak
session) that pulls every health signal the bot already produces
into a single markdown digest. Replaces the operator having to
remember to run:

  * mnq doctor
  * scripts/regime_report.py
  * scripts/variant_pruner.py

with a single ``python scripts/morning_report.py``.

Sections in the output (in this order):

  1. Doctor status      -- one row per check (ok/warn/fail)
  2. Variant fleet      -- KEEP/WATCH/PRUNE counts + named lists
  3. Drift watch        -- any variant where E_recency drifts from E
                            beyond +/- 0.05R (FADING / GROWING)
  4. Top variants       -- top 5 by E_recency (descending)

Usage
-----
    python scripts/morning_report.py
    python scripts/morning_report.py --output reports/morning_2026_05_01.md
    python scripts/morning_report.py --json    # machine-readable

Exit code is 0 always (this is a reporter). The doctor's own exit
code is preserved in the JSON output as ``doctor_exit_code``.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

DEFAULT_OUTPUT = REPO_ROOT / "reports" / "morning_report.md"

DRIFT_THRESHOLD_R = 0.05


def _load_script(name: str) -> Any:
    """Load a sibling script module by name without polluting
    sys.modules with the wrong name."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"_morning_{name}", path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"can't load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gather_doctor() -> dict[str, Any]:
    """Run mnq doctor and return a structured snapshot."""
    try:
        from mnq.cli.doctor import run_all_checks
    except ImportError as e:
        return {
            "available": False,
            "error": f"can't import doctor: {e}",
            "checks": [],
            "exit_code": None,
        }
    try:
        results = run_all_checks(strict=False)
    except Exception as e:  # noqa: BLE001 -- defensive
        return {
            "available": False,
            "error": f"{type(e).__name__}: {e}",
            "checks": [],
            "exit_code": None,
        }
    checks = [
        {"name": r.name, "status": r.status, "detail": r.detail}
        for r in results
    ]
    n_fail = sum(1 for r in results if r.status == "fail")
    n_warn = sum(1 for r in results if r.status == "warn")
    return {
        "available": True,
        "checks": checks,
        "n_total": len(results),
        "n_ok": sum(1 for r in results if r.status == "ok"),
        "n_warn": n_warn,
        "n_fail": n_fail,
        "exit_code": 1 if n_fail else 0,
    }


def _gather_variants() -> dict[str, Any]:
    """Run variant_pruner classification and return counts + buckets."""
    try:
        pruner = _load_script("variant_pruner")
    except ImportError as e:
        return {"available": False, "error": str(e)}
    try:
        rows = pruner._build_classified()  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        return {
            "available": False,
            "error": f"{type(e).__name__}: {e}",
        }
    by_bucket: dict[str, list[str]] = {
        pruner.PRUNE: [], pruner.WATCH: [], pruner.KEEP: [],
    }
    for row in rows:
        by_bucket[row["bucket"]].append(row["variant"])
    return {
        "available": True,
        "n_total": len(rows),
        "n_keep": len(by_bucket[pruner.KEEP]),
        "n_watch": len(by_bucket[pruner.WATCH]),
        "n_prune": len(by_bucket[pruner.PRUNE]),
        "keep": sorted(by_bucket[pruner.KEEP]),
        "watch": sorted(by_bucket[pruner.WATCH]),
        # Don't list every PRUNE variant in the morning report; that's
        # what variant_pruner.py is for. Just count + show first 10.
        "prune_sample": sorted(by_bucket[pruner.PRUNE])[:10],
        "rows": rows,
    }


def _gather_drift(variants: dict[str, Any]) -> list[dict[str, Any]]:
    """From the variant classification, find variants where E_recency
    drifts from E_total beyond DRIFT_THRESHOLD_R."""
    if not variants.get("available"):
        return []
    rows = variants.get("rows") or []
    drifters: list[dict[str, Any]] = []
    for row in rows:
        e = row.get("expected_expectancy_r", 0.0)
        rec = row.get("recency_weighted_expectancy_r")
        if rec is None:
            continue
        delta = rec - e
        if abs(delta) >= DRIFT_THRESHOLD_R:
            tag = "FADING" if delta < 0 else "GROWING"
            drifters.append({
                "variant": row["variant"],
                "tag": tag,
                "expected_expectancy_r": e,
                "recency_weighted_expectancy_r": rec,
                "delta_r": delta,
                "bucket": row.get("bucket"),
            })
    # Sort by abs delta desc -- biggest drift first
    drifters.sort(key=lambda d: -abs(d["delta_r"]))
    return drifters


def _gather_top_variants(
    variants: dict[str, Any], *, top_n: int = 5,
) -> list[dict[str, Any]]:
    """Top N variants by E_recency (descending). Returns rows with
    variant / E_total / E_recency / drift."""
    if not variants.get("available"):
        return []
    rows = variants.get("rows") or []
    # Filter to those with a real recency value
    scored = [
        r for r in rows
        if r.get("recency_weighted_expectancy_r") is not None
    ]
    scored.sort(
        key=lambda r: -float(r.get("recency_weighted_expectancy_r", 0.0)),
    )
    return [
        {
            "variant": r["variant"],
            "expected_expectancy_r": r.get("expected_expectancy_r", 0.0),
            "recency_weighted_expectancy_r": (
                r.get("recency_weighted_expectancy_r")
            ),
            "bucket": r.get("bucket"),
        }
        for r in scored[:top_n]
    ]


def gather_report() -> dict[str, Any]:
    """Aggregate every section into one snapshot dict."""
    doctor = _gather_doctor()
    variants = _gather_variants()
    drift = _gather_drift(variants)
    top = _gather_top_variants(variants)
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "doctor": doctor,
        "variants": variants,
        "drift_watch": drift,
        "top_variants": top,
    }


def _render_doctor_section(doctor: dict[str, Any]) -> list[str]:
    lines: list[str] = ["## Doctor", ""]
    if not doctor.get("available"):
        lines.append(f"_unavailable: {doctor.get('error')}_")
        lines.append("")
        return lines
    lines.append(
        f"* **{doctor['n_ok']}** ok · "
        f"**{doctor['n_warn']}** warn · "
        f"**{doctor['n_fail']}** fail "
        f"(of {doctor['n_total']} checks)",
    )
    lines.append("")
    lines.append("| check | status | detail |")
    lines.append("|---|---|---|")
    for c in doctor["checks"]:
        # Truncate detail to keep table readable
        detail = c["detail"]
        if len(detail) > 80:
            detail = detail[:77] + "..."
        # Escape pipe characters in detail to keep markdown table valid
        detail = detail.replace("|", "\\|")
        lines.append(f"| {c['name']} | {c['status'].upper()} | {detail} |")
    lines.append("")
    return lines


def _render_variants_section(variants: dict[str, Any]) -> list[str]:
    lines: list[str] = ["## Variant fleet", ""]
    if not variants.get("available"):
        lines.append(f"_unavailable: {variants.get('error')}_")
        lines.append("")
        return lines
    lines.append(
        f"* Total: **{variants['n_total']}** "
        f"(KEEP={variants['n_keep']} "
        f"WATCH={variants['n_watch']} "
        f"PRUNE={variants['n_prune']})",
    )
    lines.append("")
    if variants["keep"]:
        lines.append("**KEEP** -- real edge + thick sample:")
        for v in variants["keep"]:
            lines.append(f"  * `{v}`")
        lines.append("")
    if variants["watch"]:
        lines.append("**WATCH** -- promising but thin sample:")
        for v in variants["watch"]:
            lines.append(f"  * `{v}`")
        lines.append("")
    if variants["prune_sample"]:
        lines.append(
            f"**PRUNE** -- showing first {len(variants['prune_sample'])} "
            f"of {variants['n_prune']}:",
        )
        for v in variants["prune_sample"]:
            lines.append(f"  * `{v}`")
        if variants["n_prune"] > len(variants["prune_sample"]):
            lines.append(
                f"  * _(...{variants['n_prune'] - len(variants['prune_sample'])} more)_",
            )
        lines.append("")
        lines.append(
            "Run `python scripts/variant_pruner.py --bucket PRUNE` for "
            "the full deletion candidate list.",
        )
        lines.append("")
    return lines


def _render_drift_section(drift: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ["## Drift watch", ""]
    if not drift:
        lines.append(
            "_No variant has drifted by more than "
            f"+/- {DRIFT_THRESHOLD_R:.3f}R between E_total and E_recency._",
        )
        lines.append("")
        return lines
    lines.append(
        f"**{len(drift)}** variant(s) with E_recency drifted from "
        f"E_total by more than +/- {DRIFT_THRESHOLD_R:.3f}R:",
    )
    lines.append("")
    lines.append("| variant | tag | E_total | E_recency | delta | bucket |")
    lines.append("|---|---|---:|---:|---:|---|")
    for d in drift:
        lines.append(
            f"| `{d['variant']}` | **{d['tag']}** | "
            f"{d['expected_expectancy_r']:+.3f}R | "
            f"{d['recency_weighted_expectancy_r']:+.3f}R | "
            f"{d['delta_r']:+.3f}R | {d['bucket']} |"
        )
    lines.append("")
    return lines


def _render_top_section(top: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ["## Top variants by E_recency", ""]
    if not top:
        lines.append("_No variants with calibrated E_recency._")
        lines.append("")
        return lines
    lines.append("| rank | variant | E_total | E_recency | bucket |")
    lines.append("|---:|---|---:|---:|---|")
    for i, r in enumerate(top, start=1):
        lines.append(
            f"| {i} | `{r['variant']}` | "
            f"{r['expected_expectancy_r']:+.4f}R | "
            f"{r['recency_weighted_expectancy_r']:+.4f}R | "
            f"{r['bucket']} |"
        )
    lines.append("")
    return lines


def render_markdown(snap: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# mnq_bot morning report -- {snap['generated_at_utc'][:19]}Z",
        "",
        "Consolidated operator status: doctor + variant fleet + drift "
        "+ top variants. Generated by `scripts/morning_report.py`.",
        "",
    ]
    lines += _render_doctor_section(snap["doctor"])
    lines += _render_variants_section(snap["variants"])
    lines += _render_drift_section(snap["drift_watch"])
    lines += _render_top_section(snap["top_variants"])
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
    args = p.parse_args(argv)

    snap = gather_report()

    if args.json:
        # Trim variant rows from JSON output -- they're large and the
        # downstream consumer can re-run variant_pruner if they need
        # the full list.
        json_snap = dict(snap)
        if json_snap.get("variants", {}).get("available"):
            v = dict(json_snap["variants"])
            v.pop("rows", None)
            json_snap["variants"] = v
        print(json.dumps(json_snap, indent=2, default=str))
        return 0

    md = render_markdown(snap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    print(f"wrote {args.output}")
    # One-line summary on stdout
    doctor = snap["doctor"]
    variants = snap["variants"]
    drift = snap["drift_watch"]
    if doctor.get("available"):
        d_summary = (
            f"doctor: {doctor['n_ok']} ok / {doctor['n_warn']} warn / "
            f"{doctor['n_fail']} fail"
        )
    else:
        d_summary = "doctor: unavailable"
    if variants.get("available"):
        v_summary = (
            f"variants: KEEP={variants['n_keep']} "
            f"WATCH={variants['n_watch']} PRUNE={variants['n_prune']}"
        )
    else:
        v_summary = "variants: unavailable"
    drift_summary = (
        f"drift: {len(drift)} variant(s) past +/-{DRIFT_THRESHOLD_R}R"
    )
    print(f"summary: {d_summary} | {v_summary} | {drift_summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
