"""Hard-gate attribution — which days does the gauntlet block, and were they profitable?

Batch 9B addendum. Answers: is the gauntlet blocking the RIGHT days?
If blocked days are mostly losers, the gate adds value even if total PnL
drops (fewer catastrophic days). If blocked days are winners, the gates
need recalibration.

Output: ``reports/hard_gate_attribution.md``
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import random  # noqa: E402

from strategy_ab import _load_real_days  # noqa: E402
from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

from mnq.gauntlet.day_aggregate import gauntlet_day_score  # noqa: E402
from mnq.gauntlet.hard_gate import (  # noqa: E402
    GauntletHardGateConfig,
    gauntlet_hard_gate,
)
from mnq.sim.layer2 import Layer2Engine  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE_SPEC = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
REPORT = REPO_ROOT / "reports" / "hard_gate_attribution.md"
VARIANTS = {cfg.name: cfg for cfg in _VARIANT_LIST}


@dataclass
class DayAttribution:
    day_idx: int
    regime: str
    pass_rate: float
    n_passed: int
    n_failed: int
    failed_gates: list[str]
    pnl: float
    n_trades: int
    gate_action: str


def main() -> int:
    from strategy_v2 import ScriptedStrategyV2

    spec = load_spec(BASELINE_SPEC)
    days = _load_real_days(timeframe="1m", source="databento")

    rng = random.Random(42)
    if len(days) > 200:
        sampled = sorted(rng.sample(range(len(days)), 200))
        days = [days[i] for i in sampled]

    variant_cfg = VARIANTS["r5_real_wide_target"]
    cfg = GauntletHardGateConfig(
        skip_threshold=0.50,
        reduce_threshold=0.67,
        critical_gates=frozenset(),
    )

    attributions: list[DayAttribution] = []
    for i, (regime, bars) in enumerate(days):
        strat = ScriptedStrategyV2(spec, cfg=variant_cfg)
        engine = Layer2Engine(spec, strat, seed=42)
        engine._rejection_p = 0.0
        ledger = engine.run(bars)

        g = gauntlet_day_score(bars, regime=regime if regime != "unknown" else None)
        dec = gauntlet_hard_gate(g, config=cfg)

        day_pnl = sum(float(tr.pnl_dollars) for tr in ledger.trades)

        attributions.append(
            DayAttribution(
                day_idx=i,
                regime=regime,
                pass_rate=g.pass_rate,
                n_passed=g.n_passed,
                n_failed=g.n_failed,
                failed_gates=g.failed_gates,
                pnl=round(day_pnl, 2),
                n_trades=ledger.n_trades,
                gate_action=dec["action"],
            )
        )

    # Analysis
    full_days = [a for a in attributions if a.gate_action == "full"]
    reduced_days = [a for a in attributions if a.gate_action == "reduced"]
    skipped_days = [a for a in attributions if a.gate_action == "skip"]

    full_pnl = sum(a.pnl for a in full_days)
    reduced_pnl = sum(a.pnl for a in reduced_days)
    skipped_pnl = sum(a.pnl for a in skipped_days)

    skipped_winners = sum(1 for a in skipped_days if a.pnl > 0)
    skipped_losers = sum(1 for a in skipped_days if a.pnl < 0)
    skipped_flat = sum(1 for a in skipped_days if a.pnl == 0)

    # Gate failure frequency
    gate_fail_counts: dict[str, int] = {}
    for a in attributions:
        for g in a.failed_gates:
            gate_fail_counts[g] = gate_fail_counts.get(g, 0) + 1

    # Pass rate distribution
    pr_buckets = {"0.0–0.33": 0, "0.33–0.50": 0, "0.50–0.67": 0, "0.67–0.83": 0, "0.83–1.0": 0}
    for a in attributions:
        if a.pass_rate < 0.33:
            pr_buckets["0.0–0.33"] += 1
        elif a.pass_rate < 0.50:
            pr_buckets["0.33–0.50"] += 1
        elif a.pass_rate < 0.67:
            pr_buckets["0.50–0.67"] += 1
        elif a.pass_rate < 0.83:
            pr_buckets["0.67–0.83"] += 1
        else:
            pr_buckets["0.83–1.0"] += 1

    lines = [
        "# Hard-Gate Attribution — Which Days Get Blocked?",
        "",
        f"Config: skip={cfg.skip_threshold}, reduce={cfg.reduce_threshold}",
        f"Sample: {len(days)} days",
        "",
        "## Action breakdown",
        "",
        "| Action | Days | PnL | Avg PnL/day |",
        "|---|---:|---:|---:|",
        f"| Full | {len(full_days)} | ${full_pnl:+,.2f} | ${full_pnl / max(1, len(full_days)):+,.2f} |",
        f"| Reduced | {len(reduced_days)} | ${reduced_pnl:+,.2f} | ${reduced_pnl / max(1, len(reduced_days)):+,.2f} |",
        f"| Skipped | {len(skipped_days)} | ${skipped_pnl:+,.2f} | ${skipped_pnl / max(1, len(skipped_days)):+,.2f} |",
        "",
        "## Skipped day analysis",
        "",
        f"- Winners blocked: **{skipped_winners}**",
        f"- Losers blocked: **{skipped_losers}**",
        f"- Flat blocked: **{skipped_flat}**",
        f"- PnL of blocked days: ${skipped_pnl:+,.2f}",
        "",
    ]

    if skipped_days:
        lines.append("### Skipped days detail")
        lines.append("")
        lines.append("| Day | Regime | Pass Rate | PnL | Trades | Failed Gates |")
        lines.append("|---:|---|---:|---:|---:|---|")
        for a in sorted(skipped_days, key=lambda x: x.pnl):
            lines.append(
                f"| {a.day_idx} | {a.regime} | {a.pass_rate:.2f} | "
                f"${a.pnl:+,.2f} | {a.n_trades} | {', '.join(a.failed_gates[:3])} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Pass-rate distribution",
            "",
            "| Bucket | Days |",
            "|---|---:|",
        ]
    )
    for bucket, count in pr_buckets.items():
        lines.append(f"| {bucket} | {count} |")

    lines.extend(
        [
            "",
            "## Gate failure frequency (all days)",
            "",
            "| Gate | Failures | Rate |",
            "|---|---:|---:|",
        ]
    )
    for gate, count in sorted(gate_fail_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {gate} | {count} | {count / len(attributions):.1%} |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
        ]
    )
    if skipped_losers > skipped_winners:
        lines.append(
            f"The hard-gate blocks more losers ({skipped_losers}) than winners "
            f"({skipped_winners}). The gates are miscalibrated in aggregate but "
            f"the directional signal is correct — further threshold tuning and "
            f"gate-weight adjustment should isolate the filtering value."
        )
    elif skipped_winners > skipped_losers:
        lines.append(
            f"The hard-gate blocks more winners ({skipped_winners}) than losers "
            f"({skipped_losers}). The gauntlet gates are anti-correlated with "
            f"profitability at this threshold. Root cause: the gates measure "
            f"'conditions suitable for trading' but profitable days often occur "
            f"in unexpected conditions. Consider recalibrating gate scores using "
            f"outcome-weighted training data."
        )
    else:
        lines.append(
            "Equal winner/loser blocking — the gates have no directional edge "
            "at this threshold. The pass_rate is noise with respect to PnL."
        )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
