"""Firm-filtered strategy vs. a naive baseline.

Phase 3: the Firm's filter gauntlet (vol filter, trend alignment, hard-pause,
order-flow proxy, directional bias, time windows, loss cooldown) is only
justified if it beats a dumb baseline on the same tape. This script runs:

* **filtered**: the current winning variant (default
  ``r5_real_wide_target`` — full gauntlet ON).
* **baseline**: the ``v1_replica`` config (raw EMA-cross scalper, no filters).

Compares them on the real 15-day MNQ sample, paired by day, with a
bootstrap CI on the per-day PnL difference (filtered − baseline). The
interpretation follows the Firm accountability charter:

    * If the CI for the daily lift excludes zero AND is positive → the
      Firm gauntlet has earned its keep.
    * If the CI crosses zero → the filter is paying for itself in trade
      count only, and the next Firm Red Team review should decide whether
      any gauntlet component can be dropped.

Usage:

    python scripts/firm_vs_baseline.py
    python scripts/firm_vs_baseline.py --filtered t16_r5_long_only
    python scripts/firm_vs_baseline.py --baseline v1_replica --n-boot 5000
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from strategy_ab import _load_real_days, _run_variant  # noqa: E402
from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

from mnq.eta_v3.gate import apex_gate  # noqa: E402
from mnq.core.types import Bar as _MnqBar  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "firm_vs_baseline.md"

VARIANTS = {cfg.name: cfg for cfg in _VARIANT_LIST}


def _synthetic_day_apex_pm_output(
    day_index: int, day_key: str | None = None, *, base_probability: float = 0.6, seed: int = 42
) -> dict:
    """Deterministic per-day PM output carrying an Apex V3 summary.

    The real 6-stage shim is pure overhead here — the gate only reads
    ``verdict`` + ``payload.eta_v3.delta``. We produce those deterministically
    from a seeded hash of ``(seed, day_key or day_index)`` so runs are
    reproducible and so the same day always produces the same gate decision.

    The synthesis mirrors PM's fold-in math:

        blended = 0.80 * base + 0.20 * (voice_agree / 15.0)
        bonus   = 0.05 if strong AND engine_live AND go_like else 0
        penalty = 0.05 if strong AND direction_conflict AND go_like else 0
        delta   = blended + bonus - penalty - base

    voice_agree is drawn from a discrete distribution with slightly more
    mass around 7-9/15 (reflects real Apex-engine behavior — rarely 0/15,
    rarely 15/15). pm_final is drawn normal(mean=42, std=12). direction
    conflict fires on ~15% of days.
    """
    key = day_key if day_key is not None else f"day_{day_index}"
    h = hashlib.sha256(f"{seed}::{key}".encode()).hexdigest()
    # Three independent draws from the hash (8 hex chars each)
    u1 = int(h[0:8], 16) / 0xFFFFFFFF  # voice_agree
    u2 = int(h[8:16], 16) / 0xFFFFFFFF  # pm_final
    u3 = int(h[16:24], 16) / 0xFFFFFFFF  # direction conflict

    # voice_agree in 0..15, biased toward middle
    # Triangular-ish distribution: avg of two uniforms → triangular [0,1]
    va_uniform = (u1 + int(h[24:32], 16) / 0xFFFFFFFF) / 2.0
    voice_agree = int(round(va_uniform * 15))
    voice_agree = max(0, min(15, voice_agree))

    # pm_final in ~[0, 90]: uniform on [0,1] → center at 42, stretch
    pm_final = 42.0 + (u2 - 0.5) * 60.0
    pm_final = max(0.0, min(90.0, pm_final))

    engine_live = pm_final >= 40.0
    strong = voice_agree >= 12
    direction_conflict = u3 < 0.15

    blended = 0.80 * base_probability + 0.20 * (voice_agree / 15.0)
    bonus = 0.05 if (strong and engine_live) else 0.0
    penalty = 0.05 if (strong and direction_conflict) else 0.0
    adjusted = max(0.0, min(1.0, blended + bonus - penalty))
    delta = adjusted - base_probability

    return {
        "verdict": "GO",
        "probability": adjusted,
        "payload": {
            "eta_v3": {
                "consumed": True,
                "voice_agree": voice_agree,
                "pm_final": pm_final,
                "engine_live": engine_live,
                "strong_corroboration": strong,
                "verdict_alignment": -1 if direction_conflict else 1,
                "verdict_alignment_label": "CONFLICT" if direction_conflict else "MATCH",
                "base_probability": base_probability,
                "adjusted_probability": adjusted,
                "delta": delta,
                "blend_weight": 0.20,
                "bonus_applied": bonus,
                "penalty_applied": penalty,
            },
        },
    }


def _apply_apex_gate_to_day_pnls(
    day_pnls: list[float],
    day_keys: list[str] | None = None,
    *,
    seed: int = 42,
    apex_source: str = "synthetic",
    day_bars: list[list[_MnqBar]] | None = None,
) -> tuple[list[float], list[dict]]:
    """Apply the Apex V3 gate to each day's PnL.

    For every day we build a PM output dict — either via the seeded-hash
    synthesizer (``apex_source='synthetic'``, default) or via a real
    per-day run of ``eta_v3_framework.firm_engine.evaluate()`` over the
    day's bars (``apex_source='real'``, requires ``day_bars``). The
    resulting dict is passed to ``apex_gate``, and the day's PnL is
    multiplied by the gate's ``size_mult`` (1.0 / 0.5 / 0.0). The gate
    decision is returned so the report can tally {full, reduced, skip}
    counts and include voice_agree / delta diagnostics.
    """
    if apex_source == "real":
        if day_bars is None:
            raise ValueError("apex_source='real' requires day_bars")
        if len(day_bars) != len(day_pnls):
            raise ValueError(f"day_bars has {len(day_bars)} days, day_pnls has {len(day_pnls)}")
        # Import here so synthetic-path callers don't pay the eta_v3_framework
        # import cost (it pulls in numpy, etc. via indicator_state).
        from real_eta_driver import day_pm_output_from_real_apex

    gated: list[float] = []
    decisions: list[dict] = []
    for i, pnl in enumerate(day_pnls):
        if apex_source == "real":
            pm_out = day_pm_output_from_real_apex(day_bars[i])
        else:
            key = day_keys[i] if day_keys and i < len(day_keys) else None
            pm_out = _synthetic_day_apex_pm_output(i, day_key=key, seed=seed)
        decision = apex_gate(pm_out)
        gated.append(pnl * decision["size_mult"])
        decision = dict(decision)
        decision["delta"] = pm_out["payload"]["eta_v3"]["delta"]
        decision["voice_agree"] = pm_out["payload"]["eta_v3"]["voice_agree"]
        decision["apex_source"] = apex_source
        decisions.append(decision)
    return gated, decisions


def _paired_daily_lift_ci(
    filtered_daily: list[float],
    baseline_daily: list[float],
    *,
    n_boot: int = 2000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Paired bootstrap: sample day indices with replacement, sum lift."""
    if not filtered_daily or not baseline_daily or len(filtered_daily) != len(baseline_daily):
        return (0.0, 0.0, 0.0)
    diffs = [f - b for f, b in zip(filtered_daily, baseline_daily, strict=True)]
    rng = random.Random(seed)
    boots: list[float] = []
    n = len(diffs)
    for _ in range(n_boot):
        resample = [diffs[rng.randrange(n)] for _ in range(n)]
        boots.append(sum(resample))
    boots.sort()
    lo = boots[int(0.025 * n_boot)]
    hi = boots[int(0.975 * n_boot) - 1]
    return (sum(diffs), lo, hi)


def compare(
    filtered_name: str = "r5_real_wide_target",
    baseline_name: str = "v1_replica",
    *,
    timeframe: str = "1m",
    n_boot: int = 2000,
    with_apex_gate: bool = False,
    apex_seed: int = 42,
    apex_source: str = "synthetic",
    data_source: str = "rth_csv",
    days_tail: int | None = None,
) -> dict:
    if filtered_name not in VARIANTS:
        raise KeyError(f"unknown filtered variant: {filtered_name}")
    if baseline_name not in VARIANTS:
        raise KeyError(f"unknown baseline variant: {baseline_name}")
    if apex_source not in ("synthetic", "real"):
        raise ValueError(f"apex_source must be 'synthetic' or 'real', got {apex_source!r}")
    if data_source not in ("rth_csv", "databento"):
        raise ValueError(f"data_source must be 'rth_csv' or 'databento', got {data_source!r}")

    spec = load_spec(BASELINE)
    days = _load_real_days(timeframe=timeframe, source=data_source, days_tail=days_tail)

    filt = _run_variant(VARIANTS[filtered_name], spec, days)
    base = _run_variant(VARIANTS[baseline_name], spec, days)

    filtered_day_pnls: list[float] = list(filt.day_pnls)
    baseline_day_pnls: list[float] = list(base.day_pnls)
    apex_decisions: list[dict] | None = None

    if with_apex_gate:
        # Gate ONLY the filtered path — Apex is co-signing the Firm's
        # decision, not the naive baseline.
        day_keys = [str(d) for d in days] if days else None
        day_bars_only = [bars for _label, bars in days] if apex_source == "real" else None
        filtered_day_pnls, apex_decisions = _apply_apex_gate_to_day_pnls(
            filtered_day_pnls,
            day_keys=day_keys,
            seed=apex_seed,
            apex_source=apex_source,
            day_bars=day_bars_only,
        )

    total_diff, lo, hi = _paired_daily_lift_ci(filtered_day_pnls, baseline_day_pnls, n_boot=n_boot)
    return {
        "filtered_name": filtered_name,
        "baseline_name": baseline_name,
        "n_days": len(days),
        "filtered": filt,
        "baseline": base,
        "filtered_day_pnls_effective": filtered_day_pnls,
        "baseline_day_pnls_effective": baseline_day_pnls,
        "total_diff": total_diff,
        "ci_lo": lo,
        "ci_hi": hi,
        "with_apex_gate": with_apex_gate,
        "apex_decisions": apex_decisions,
        "apex_source": apex_source if with_apex_gate else None,
        "data_source": data_source,
        "days_tail": days_tail,
    }


def _verdict(ci_lo: float, ci_hi: float, total_diff: float) -> str:
    if ci_lo > 0:
        return "**FIRM FILTER JUSTIFIED** — lift CI strictly positive."
    if ci_hi < 0:
        return (
            "**FIRM FILTER HARMFUL** — lift CI strictly negative; review each gauntlet component."
        )
    # CI crosses zero
    direction = "positive" if total_diff > 0 else "negative"
    return (
        f"**INCONCLUSIVE** — lift CI crosses zero. Nominal sign is {direction}; "
        "sample size or variance is the bottleneck. Collect more journal days or "
        "drop a gauntlet component with the lowest unique-contribution."
    )


def _render(d: dict) -> str:
    filt = d["filtered"]
    base = d["baseline"]
    with_gate: bool = d.get("with_apex_gate", False)
    apex_decisions: list[dict] | None = d.get("apex_decisions")
    filt_eff = d.get("filtered_day_pnls_effective") or list(filt.day_pnls)

    lines: list[str] = ["# Firm-Filtered vs. Baseline", ""]
    lines.append(f"- Days tested: **{d['n_days']}**")
    lines.append(f"- Filtered variant: `{d['filtered_name']}`")
    lines.append(f"- Baseline variant: `{d['baseline_name']}`")
    lines.append(f"- Apex V3 downstream gate: **{'ON' if with_gate else 'off'}**")
    if with_gate:
        lines.append(f"- Apex snapshot source: **{d.get('apex_source', 'synthetic')}**")
    lines.append("")
    lines.append("## Headline numbers")
    lines.append("")
    lines.append("| Metric | Filtered | Baseline | Lift |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| Trades | {filt.n_trades} | {base.n_trades} | {filt.n_trades - base.n_trades:+d} |"
    )
    filt_net_eff = sum(filt_eff)
    lines.append(
        f"| Net PnL | ${float(filt_net_eff):+,.2f} | ${float(base.net_pnl):+,.2f} | "
        f"${float(filt_net_eff) - float(base.net_pnl):+,.2f} |"
    )
    lines.append(
        f"| Win rate | {filt.win_rate:.1%} | {base.win_rate:.1%} | "
        f"{(filt.win_rate - base.win_rate) * 100:+.1f} pp |"
    )
    lines.append(
        f"| Expectancy / trade | ${float(filt.expectancy):+,.2f} | "
        f"${float(base.expectancy):+,.2f} | "
        f"${float(filt.expectancy) - float(base.expectancy):+,.2f} |"
    )
    lines.append("")
    lines.append("## Daily lift (paired, filtered − baseline)")
    lines.append("")
    lines.append(f"- Total lift: **${d['total_diff']:+,.2f}**")
    lines.append(f"- 95% bootstrap CI: **${d['ci_lo']:+,.2f} / ${d['ci_hi']:+,.2f}**")
    lines.append("")
    if with_gate and apex_decisions:
        tally = {"full": 0, "reduced": 0, "skip": 0}
        for dec in apex_decisions:
            tally[dec.get("action", "full")] = tally.get(dec.get("action", "full"), 0) + 1
        lines.append("## Apex V3 gate decisions")
        lines.append("")
        lines.append(
            f"- Full: **{tally['full']}** | Reduced: **{tally['reduced']}** | "
            f"Skipped: **{tally['skip']}** (of {len(apex_decisions)} days)"
        )
        lines.append("")
    lines.append("## Per-day ledger")
    lines.append("")
    if with_gate:
        lines.append("| Day | Filt PnL (raw) | Gate | Filt PnL (gated) | Baseline PnL | Lift |")
        lines.append("|---:|---:|:---:|---:|---:|---:|")
        for i, (fp_raw, fp_eff, bp) in enumerate(
            zip(filt.day_pnls, filt_eff, base.day_pnls, strict=True)
        ):
            action = apex_decisions[i]["action"] if apex_decisions else "—"
            va = apex_decisions[i].get("voice_agree", "—") if apex_decisions else "—"
            lines.append(
                f"| {i} | ${fp_raw:+,.2f} | {action[:4]} (va={va}) | "
                f"${fp_eff:+,.2f} | ${bp:+,.2f} | ${fp_eff - bp:+,.2f} |"
            )
    else:
        lines.append("| Day | Filtered PnL | Baseline PnL | Lift |")
        lines.append("|---:|---:|---:|---:|")
        for i, (fp, bp) in enumerate(zip(filt.day_pnls, base.day_pnls, strict=True)):
            lines.append(f"| {i} | ${fp:+,.2f} | ${bp:+,.2f} | ${fp - bp:+,.2f} |")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(_verdict(d["ci_lo"], d["ci_hi"], d["total_diff"]))
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "* The Firm's accountability charter requires every complexity "
        "addition to the system to justify itself with a measurable lift. "
        "This report is the enforcement mechanism."
    )
    lines.append(
        "* A positive lift driven entirely by *fewer* trades (i.e. the "
        "filter avoids losers) is a different qualitative result than a "
        "positive lift driven by *equally many* trades with higher "
        "expectancy. Inspect the headline table."
    )
    lines.append(
        "* If the CI crosses zero, the Firm should specify a falsification "
        "test (deadline + effect size) in `reports/firm_reviews/` and "
        "commit to stripping the gauntlet if the test fails."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Firm-filtered vs baseline comparison.")
    parser.add_argument("--filtered", type=str, default="r5_real_wide_target")
    parser.add_argument("--baseline", type=str, default="v1_replica")
    parser.add_argument("--timeframe", type=str, default="1m")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--with-apex-gate",
        action="store_true",
        help="Apply the Apex V3 downstream gate (src/mnq/eta_v3/gate.py) "
        "to the filtered variant's per-day PnLs. A deterministic per-day "
        "Apex snapshot is synthesized and routed through apex_gate().",
    )
    parser.add_argument(
        "--apex-seed",
        type=int,
        default=42,
        help="Seed for per-day Apex snapshot synthesis (default: 42).",
    )
    parser.add_argument(
        "--apex-source",
        type=str,
        choices=("synthetic", "real"),
        default="synthetic",
        help="Apex snapshot source: 'synthetic' (deterministic hash, "
        "backwards-compat default) or 'real' (per-day "
        "firm_engine.evaluate() over real bar sequences — Batch 3F).",
    )
    parser.add_argument(
        "--data-source",
        type=str,
        choices=("rth_csv", "databento"),
        default="rth_csv",
        help="Real-bar source: 'rth_csv' (~15 session-tagged days, default) "
        "or 'databento' (multi-year 1m tape, Batch 3G — use "
        "--days-tail to cap recent window).",
    )
    parser.add_argument(
        "--days-tail",
        type=int,
        default=None,
        help="When --data-source=databento, keep only the last N "
        "RTH-complete days. Default: all eligible days.",
    )
    args = parser.parse_args(argv)

    result = compare(
        filtered_name=args.filtered,
        baseline_name=args.baseline,
        timeframe=args.timeframe,
        n_boot=args.n_boot,
        with_apex_gate=args.with_apex_gate,
        apex_seed=args.apex_seed,
        apex_source=args.apex_source,
        data_source=args.data_source,
        days_tail=args.days_tail,
    )
    md = _render(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
