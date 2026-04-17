"""Run a strategy variant through the Firm's adversarial review process.

Bridges the mnq_bot simulation stack to the Firm skill installed at
``mnq_bot/firm/``.  Given a strategy variant name, this script:

1.  Runs it through :mod:`strategy_ab._run_variant` on the real MNQ RTH
    dataset (or synthetic, via ``--synthetic``).
2.  Pulls per-exit-reason / per-regime / per-side attribution, bootstrap
    CI on daily PnL, and realized-slippage distribution.
3.  Renders a one-page decision memo using the Firm's
    ``templates/decision_memo.md`` schema — all blanks auto-filled from
    the A/B + journal results.
4.  Renders a falsification-criteria block using
    ``templates/falsification.md`` with *numeric, pre-committed,
    time-bound* criteria inferred from the current run.
5.  Writes the full review to ``reports/firm_reviews/<variant>.md``.

The point is **not** to replace the six-stage adversarial debate (the
Firm skill still runs that in chat).  The point is to auto-populate the
quantitative fields so the human only has to fill in thesis, resolution,
and override rationale — the judgement parts.

Usage::

    python scripts/firm_review.py --variant r5_real_wide_target
    python scripts/firm_review.py --variant r5_real_wide_target --synthetic
    python scripts/firm_review.py --variant r5_real_wide_target --journal path.sqlite

The variant name must exist in :data:`strategy_v2.VARIANTS`.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (str(SRC), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from strategy_ab import (  # noqa: E402
    _bootstrap_ci,
    _load_real_days,
    _load_synthetic_days,
    _run_variant,
)
from strategy_v2 import VARIANTS  # noqa: E402

from mnq.spec.loader import load_spec  # noqa: E402

BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
FIRM_DIR = REPO_ROOT / "firm"
REPORTS_DIR = REPO_ROOT / "reports" / "firm_reviews"


# ---------------------------------------------------------------------------
# Optional: pull realized slippage from the live-sim journal if it exists.
# ---------------------------------------------------------------------------


DEFAULT_JOURNAL = Path("/sessions/kind-keen-faraday/data/live_sim/journal.sqlite")


@dataclass
class JournalSummary:
    n_trades: int
    mean_slip_ticks: float
    p95_slip_ticks: float
    wins: int
    total_pnl: float


def summarize_journal(path: Path) -> JournalSummary | None:
    """Read FILL_REALIZED closures and summarize realized execution.

    Returns None if the journal doesn't exist yet.
    """
    if not path.exists():
        return None
    try:
        from mnq.storage.journal import EventJournal  # noqa: E402
        from mnq.storage.schema import FILL_REALIZED  # noqa: E402
    except ImportError:
        return None

    slips: list[float] = []
    pnls: list[float] = []
    wins = 0
    journal = EventJournal(path)
    for entry in journal.replay(event_types=(FILL_REALIZED,)):
        p = entry.payload
        if "pnl_dollars" not in p or "entry_ts" not in p:
            continue
        try:
            pnl = float(p["pnl_dollars"])
            slip = float(p.get("slippage_ticks", 0.0))
        except (TypeError, ValueError):
            continue
        pnls.append(pnl)
        slips.append(slip)
        if pnl > 0:
            wins += 1

    if not pnls:
        return None
    slips_sorted = sorted(slips)
    p95 = slips_sorted[int(0.95 * (len(slips_sorted) - 1))] if slips_sorted else 0.0
    return JournalSummary(
        n_trades=len(pnls),
        mean_slip_ticks=sum(slips) / len(slips),
        p95_slip_ticks=p95,
        wins=wins,
        total_pnl=sum(pnls),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_money(d: Decimal | float) -> str:
    x = float(d)
    return f"${x:+,.2f}"


def _render_memo(
    variant_name: str,
    cfg_dict: dict[str, Any],
    result: Any,
    ci: tuple[float, float, float],
    source: str,
    n_days: int,
    journal: JournalSummary | None,
) -> str:
    total, lo, hi = ci
    exp_per_trade = (
        float(result.net_pnl) / result.n_trades if result.n_trades else 0.0
    )
    exp_r = exp_per_trade / (cfg_dict["risk_ticks"] * 0.5)  # 1 tick = $0.50 MNQ
    wr_pct = result.win_rate * 100

    # ----- falsification thresholds (numeric, time-bound) -----
    review_date = date.today() + timedelta(days=30)

    lines: list[str] = []
    lines.append(f"# Firm Review — `{variant_name}`")
    lines.append("")
    lines.append(
        f"_Auto-generated {date.today().isoformat()} from "
        f"`scripts/firm_review.py` · data: {source} ({n_days} days)_"
    )
    lines.append("")
    lines.append("## Configuration")
    lines.append("")
    lines.append("```python")
    for k, v in cfg_dict.items():
        lines.append(f"{k} = {v!r}")
    lines.append("```")
    lines.append("")

    # ----- Quant section (stage 1) -----
    lines.append("## Stage 1 — Quant (Spec)")
    lines.append("")
    lines.append(f"- **Sample size:** {result.n_trades} trades over {n_days} days")
    lines.append(f"- **Net PnL:** {_fmt_money(result.net_pnl)}")
    lines.append(f"- **Expectancy / trade:** {_fmt_money(exp_per_trade)} "
                 f"(= {exp_r:+.3f} R)")
    lines.append(f"- **Win rate:** {wr_pct:.1f}%")
    lines.append(f"- **95% bootstrap CI on total PnL:** "
                 f"${lo:+.2f} / ${hi:+.2f}")
    lines.append(f"- **Risk per trade (spec):** "
                 f"{cfg_dict['risk_ticks']} ticks = ${cfg_dict['risk_ticks']*0.5:.2f}")
    lines.append("")
    lines.append("### Per-regime breakdown")
    lines.append("")
    lines.append("| Regime | n | wins | win% | net PnL |")
    lines.append("|---|---:|---:|---:|---:|")
    for reg, b in sorted(result.per_regime.items()):
        n = int(b["n"])
        wn = int(b["wins"])
        wrp = (wn / n) if n else 0.0
        lines.append(f"| `{reg}` | {n} | {wn} | {wrp:.1%} | "
                     f"{_fmt_money(Decimal(b['pnl']))} |")
    lines.append("")
    lines.append("### Per exit reason")
    lines.append("")
    lines.append("| Reason | n | net PnL |")
    lines.append("|---|---:|---:|")
    for k, b in sorted(result.per_exit_reason.items()):
        lines.append(f"| `{k}` | {int(b['n'])} | "
                     f"{_fmt_money(Decimal(b['pnl']))} |")
    lines.append("")

    # ----- Red Team (stage 2) -----
    lines.append("## Stage 2 — Red Team (Attack)")
    lines.append("")
    attacks: list[str] = []
    if result.n_trades < 30:
        attacks.append(
            f"**Sample size.** n={result.n_trades} trades over {n_days} days is "
            "well below the 30-trade threshold for estimating expectancy. The "
            "bootstrap CI straddles zero — we cannot reject a null of zero edge."
        )
    if lo < 0 < hi:
        attacks.append(
            f"**CI includes zero.** 95% bootstrap on total PnL is "
            f"[${lo:+.2f}, ${hi:+.2f}]. The lower bound shows a plausible net loss "
            "of this magnitude over the same 15-day window."
        )
    if wr_pct < 40 and exp_per_trade > 0:
        attacks.append(
            f"**Win rate is low ({wr_pct:.1f}%).** Expectancy depends on 1-2 "
            "fat-tail winners. If the target-fill distribution changes (e.g. more "
            "choppy days), the strategy goes negative fast."
        )
    if result.per_regime:
        worst = min(result.per_regime.items(), key=lambda kv: float(kv[1]["pnl"]))
        reg, b = worst
        if Decimal(b["pnl"]) < 0:
            attacks.append(
                f"**Regime bleed.** `{reg}` contributes "
                f"{_fmt_money(Decimal(b['pnl']))} across {int(b['n'])} trades. "
                "If this regime dominates the next month, net PnL turns negative."
            )
    if journal is not None and journal.mean_slip_ticks > 1.0:
        attacks.append(
            f"**Slippage drag.** Live-sim journal shows "
            f"{journal.mean_slip_ticks:+.2f} ticks mean slippage "
            f"(p95 {journal.p95_slip_ticks:+.1f}). At "
            f"{cfg_dict['risk_ticks']}-tick stops this is a material cost drag."
        )
    if not attacks:
        attacks.append(
            "**Red Team was unable to find a primary attack — which is itself "
            "a process flag.** Rerun Red Team before shipping."
        )
    for a in attacks:
        lines.append(f"- {a}")
    lines.append("")

    # ----- Risk (stage 3) -----
    lines.append("## Stage 3 — Risk Manager (Sizing)")
    lines.append("")
    # Very conservative Kelly given the small sample: shrink by 1/4 and cap.
    if result.n_trades > 0 and exp_r > 0 and result.win_rate > 0:
        b_payoff = cfg_dict["rr"]
        p = result.win_rate
        q = 1 - p
        kelly_full = (b_payoff * p - q) / b_payoff
        kelly_quarter = max(0.0, min(kelly_full * 0.25, 0.02))
    else:
        kelly_full = 0.0
        kelly_quarter = 0.0
    lines.append(f"- **Full Kelly estimate:** {kelly_full:.3f} "
                 "(uses observed WR and spec rr)")
    lines.append(f"- **Fractional Kelly (1/4, capped 2%):** "
                 f"{kelly_quarter*100:.2f}% of equity per trade")
    lines.append(f"- **Risk per trade in dollars:** "
                 f"${cfg_dict['risk_ticks']*0.5:.2f} per contract, 1 contract")
    lines.append("- **Daily stop:** -3R (hard breaker at -$60 on a 40-tick risk)")
    lines.append("- **Weekly stop:** -8R")
    lines.append("- **Drawdown kill:** -15R peak-to-trough")
    lines.append(
        "- **Comment:** sample is too small for sizing above 1 contract. "
        "Kelly is directional only; position size is dictated by the "
        "risk budget, not the calculation above, until n>50 trades."
    )
    lines.append("")

    # ----- Macro (stage 4) -----
    lines.append("## Stage 4 — Macro (Regime)")
    lines.append("")
    lines.append("- **Instrument:** MNQ (micro E-mini Nasdaq-100)")
    lines.append(f"- **Session filter:** {cfg_dict.get('morning_window')} AM "
                 f"/ {cfg_dict.get('afternoon_window')} PM (bar index, 1m)")
    lines.append(f"- **Volatility gate:** "
                 f"stdev_max={cfg_dict.get('vol_filter_stdev_max', 0)}, "
                 f"hard_pause={cfg_dict.get('vol_hard_pause_stdev', 0)}")
    lines.append(
        "- **Competence:** per-regime table above is the competence matrix. "
        "Do NOT trade this variant when realized 1m stdev exceeds the hard-pause "
        "level — it is un-tested territory for this config."
    )
    lines.append("")

    # ----- Micro (stage 5) -----
    lines.append("## Stage 5 — Micro (Execution)")
    lines.append("")
    if journal is not None:
        lines.append(f"- **Journal trades:** {journal.n_trades}")
        lines.append(f"- **Mean slippage:** {journal.mean_slip_ticks:+.2f} ticks")
        lines.append(f"- **p95 slippage:** {journal.p95_slip_ticks:+.2f} ticks")
        lines.append(f"- **Journal net PnL:** {_fmt_money(journal.total_pnl)}")
    else:
        lines.append("- **Journal:** not yet populated for this variant. "
                     "Run `scripts/live_sim.py --real --variant "
                     f"{variant_name}` to populate.")
    lines.append(
        "- **Fill assumption:** limit entries with 1-tick simulated slippage, "
        "market exits on stop/target. Production must match this assumption "
        "on the broker side or expectancy shifts."
    )
    lines.append("")

    # ----- PM (stage 6) -----
    lines.append("## Stage 6 — PM (Decide)")
    lines.append("")
    ship = (
        result.n_trades >= 8
        and exp_per_trade > 0
        and hi > 0  # CI upper bound positive
    )
    if ship:
        verdict = (
            "**SHIP TO INTERNAL-SIM** with mandatory 30-day observation. Do not "
            "escalate to paper or live until falsification window completes clean."
        )
    else:
        verdict = (
            "**HOLD.** Sample or expectancy does not clear the ship threshold "
            "(n≥8, E[trade]>0, CI upper>0)."
        )
    lines.append(f"- **Verdict:** {verdict}")
    lines.append("- **Monitoring:** every 10 new trades, rerun this review and "
                 "diff against prior memo")
    lines.append("")

    # ----- Decision memo (template) -----
    lines.append("## One-page Decision Memo")
    lines.append("")
    lines.append("```")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("STRATEGY DECISION MEMO")
    lines.append(f"ID: {variant_name}   Date: {date.today().isoformat()}   "
                 "Author: edward avila")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("THESIS (one sentence)")
    lines.append(f"  EMA9/EMA21 cross on MNQ 1m RTH with vol + flow gates, "
                 f"rr={cfg_dict['rr']}, risk={cfg_dict['risk_ticks']}t, "
                 "captures afternoon drift more than morning noise.")
    lines.append("")
    lines.append("EVIDENCE (3 bullets, numeric)")
    lines.append(f"  • {result.n_trades} trades / {n_days} days, "
                 f"net {_fmt_money(result.net_pnl)}, "
                 f"WR {wr_pct:.1f}%, E[trade] {_fmt_money(exp_per_trade)}")
    lines.append(f"  • 95% boot CI on total PnL: "
                 f"${lo:+.2f} / ${hi:+.2f}")
    if result.per_regime:
        best = max(result.per_regime.items(), key=lambda kv: float(kv[1]["pnl"]))
        lines.append(f"  • Best regime bucket: `{best[0]}` "
                     f"({int(best[1]['n'])} trades, "
                     f"{_fmt_money(Decimal(best[1]['pnl']))})")
    else:
        lines.append("  • (regime attribution pending)")
    lines.append("")
    lines.append("RED TEAM'S PRIMARY DISSENT (verbatim)")
    lines.append(f"  {attacks[0][:200]}")
    lines.append("")
    lines.append("RESOLUTION")
    lines.append("  [ ] Fixed — how: _______")
    lines.append("  [x] Accepted as surviving risk — monitoring: "
                 "rerun memo every 10 trades")
    lines.append("  [ ] Overridden — rationale: _______")
    lines.append("")
    lines.append("SIZING")
    lines.append(f"  Risk per trade: {kelly_quarter*100:.2f}%   "
                 f"Kelly fraction: {kelly_quarter:.3f} (1/4 capped)")
    lines.append("  Daily stop: -3R   Weekly: -8R   DD kill: -15R")
    lines.append("")
    lines.append("FALSIFICATION")
    lines.append(f"  I abandon this by {review_date.isoformat()} if ANY of:")
    lines.append("  • Live expectancy < +0.05R across first 30 new trades")
    lines.append("  • Slippage p95 exceeds +3.0 ticks over any 10-trade window")
    lines.append("  • Net PnL < lower-CI bound "
                 f"(${lo:+.2f}) for trailing 15 days")
    lines.append("  • Any single loss exceeds "
                 f"{cfg_dict['risk_ticks']*3} ticks (= 3x intended risk)")
    lines.append("")
    lines.append("MONITORING")
    lines.append("  First review: after 10 trades")
    lines.append("  Success: E[trade] ≥ +0.10R, DD ≤ -5R")
    lines.append("  Failure:  E[trade] ≤ 0, OR DD ≥ -15R")
    lines.append("")
    lines.append("SIGNATURE: __________")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, help="StrategyConfig.name to review")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--timeframe", choices=["1m", "5m"], default="1m")
    ap.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    ap.add_argument("--out", default=None,
                    help="output md path (default reports/firm_reviews/<variant>.md)")
    ap.add_argument("--summary-json", default=None, help="optional JSON summary path")
    args = ap.parse_args(argv)

    cfg = next((v for v in VARIANTS if v.name == args.variant), None)
    if cfg is None:
        print(f"ERROR: variant {args.variant!r} not found. "
              f"Known variants:", file=sys.stderr)
        for v in VARIANTS:
            print(f"  - {v.name}", file=sys.stderr)
        return 2

    spec = load_spec(BASELINE)
    if args.synthetic:
        days = _load_synthetic_days(20)
        source = "synthetic"
    else:
        days = _load_real_days(args.timeframe)
        source = f"real_mnq_{args.timeframe}_rth"

    if not days:
        print("ERROR: no days loaded", file=sys.stderr)
        return 2

    print(f"Running {args.variant} on {len(days)} days from {source}...")
    result = _run_variant(cfg, spec, days, seed=0)
    ci = _bootstrap_ci(result.day_pnls)
    journal = summarize_journal(Path(args.journal))

    # Convert dataclass fields to dict for rendering.
    cfg_dict = {
        k: getattr(cfg, k)
        for k in ("rr", "risk_ticks", "time_stop_bars",
                  "cross_magnitude_min", "vol_filter_stdev_max",
                  "vol_hard_pause_stdev", "trend_align_bars",
                  "orderflow_proxy_min", "morning_window",
                  "afternoon_window", "loss_cooldown_bars")
        if hasattr(cfg, k)
    }

    md = _render_memo(
        variant_name=args.variant,
        cfg_dict=cfg_dict,
        result=result,
        ci=ci,
        source=source,
        n_days=len(days),
        journal=journal,
    )

    out_path = Path(args.out) if args.out else REPORTS_DIR / f"{args.variant}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    print(f"Wrote Firm review to {out_path}")
    print(f"  net_pnl={_fmt_money(result.net_pnl)}  "
          f"n={result.n_trades}  wr={result.win_rate:.1%}  "
          f"CI=[${ci[1]:+.2f}, ${ci[2]:+.2f}]")

    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(
            json.dumps(
                {
                    "variant": args.variant,
                    "n_trades": result.n_trades,
                    "net_pnl": float(result.net_pnl),
                    "win_rate": result.win_rate,
                    "ci": list(ci),
                    "source": source,
                    "n_days": len(days),
                    "journal_trades": journal.n_trades if journal else 0,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
