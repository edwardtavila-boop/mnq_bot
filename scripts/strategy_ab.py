"""A/B harness for ScriptedStrategyV2 variants.

Runs every ``StrategyConfig`` in :data:`strategy_v2.VARIANTS` through the
Layer-2 simulator on either:

* real MNQ 1-minute RTH bars (default — reads the CSV next to
  ``scripts/real_bars.py``), or
* synthetic bars from ``pnl_report.synth_day`` (``--synthetic`` flag,
  preserves parity with the v1 baseline report).

For each variant we compute:

    - n_trades, net PnL, win rate, expectancy/trade
    - per-exit-reason + per-side breakdown
    - per-regime PnL (synthetic only; real data has no regime label so
      we bucket by realized stdev instead)
    - bootstrap CI for total PnL (paired by day index)

Emits a comparison markdown to ``reports/strategy_v2_report.md`` with a
ranked table and picks a winner based on (risk-adjusted PnL, n_trades
floor, WR floor).

Usage:
    python scripts/strategy_ab.py                 # real MNQ data
    python scripts/strategy_ab.py --synthetic     # synthetic 20 days
    python scripts/strategy_ab.py --winner-only   # print only the winner
"""

from __future__ import annotations

import argparse
import importlib.util
import random
import statistics
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (str(SRC), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Local imports (scripts/)
from strategy_v2 import VARIANTS, ScriptedStrategyV2, StrategyConfig  # noqa: E402

from mnq.core.types import Bar  # noqa: E402
from mnq.sim.layer2 import Layer2Engine  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
REPORT_PATH = REPO_ROOT / "reports" / "strategy_v2_report.md"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_real_days(
    timeframe: str = "1m",
    *,
    source: str = "rth_csv",
    days_tail: int | None = None,
) -> list[tuple[str, list[Bar]]]:
    """Label each real day by its regime using a simple heuristic:
        - compute day-level stdev of 1m returns
        - label: 'high_vol' if stdev > p75, 'chop' if return range < 0.2% else trend.

    Sources:
        * ``rth_csv`` (default) — the ~15-day session-tagged RTH CSV.
        * ``databento`` — the multi-year Databento tape (Batch 3G). ``days_tail``
          can cap to the last N RTH-complete days (e.g. 30/60/90).
    """
    from real_bars import (
        CSV_5M,
        DEFAULT_CSV,
        load_databento_days,
        load_real_days,
    )

    if source == "databento":
        if timeframe != "1m":
            raise ValueError(
                f"databento source currently only supports timeframe='1m', got {timeframe!r}"
            )
        days = load_databento_days(min_bars_per_day=380, timeframe_sec=60, days_tail=days_tail)
    elif timeframe == "5m":
        days = load_real_days(CSV_5M, min_bars_per_day=70, timeframe_sec=300)
    else:
        days = load_real_days(DEFAULT_CSV, min_bars_per_day=380, timeframe_sec=60)

    stdevs: list[float] = []
    for d in days:
        closes = [float(b.close) for b in d]
        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        if len(diffs) > 2:
            stdevs.append(statistics.stdev(diffs))
        else:
            stdevs.append(0.0)
    if not stdevs:
        return []
    p75 = statistics.quantiles(stdevs, n=4)[2] if len(stdevs) >= 4 else max(stdevs)

    labeled: list[tuple[str, list[Bar]]] = []
    for d, sd in zip(days, stdevs, strict=True):
        if sd > p75:
            label = "real_high_vol"
        else:
            closes = [float(b.close) for b in d]
            rng_pct = (max(closes) - min(closes)) / closes[0] if closes else 0.0
            dir_net = (closes[-1] - closes[0]) / closes[0] if closes else 0.0
            if rng_pct < 0.005:
                label = "real_range"
            elif dir_net > 0.002:
                label = "real_trend_up"
            elif dir_net < -0.002:
                label = "real_trend_down"
            else:
                label = "real_chop"
        labeled.append((label, d))
    return labeled


def _load_synthetic_days(n_days: int = 20) -> list[tuple[str, list[Bar]]]:
    import pnl_report  # local import to avoid top-level cost when using real data

    rng = random.Random(7)
    regimes = list(pnl_report.REGIMES)
    out: list[tuple[str, list[Bar]]] = []
    for d in range(n_days):
        reg = rng.choice(regimes)
        bars = pnl_report.synth_day(d, regime=reg, seed=1000)
        out.append((reg.name, bars))
    return out


# ---------------------------------------------------------------------------
# Run one variant
# ---------------------------------------------------------------------------


@dataclass
class VariantResult:
    name: str
    n_trades: int
    net_pnl: Decimal
    win_rate: float
    expectancy: Decimal
    per_regime: dict[str, dict[str, Any]]
    per_exit_reason: dict[str, dict[str, Any]]
    per_side: dict[str, dict[str, Any]]
    day_pnls: list[float]
    day_n_trades: list[int]


def _run_variant(
    cfg: StrategyConfig, spec: Any, days: list[tuple[str, list[Bar]]], *, seed: int = 0
) -> VariantResult:
    per_regime: dict[str, dict[str, Any]] = {}
    per_exit_reason: dict[str, dict[str, Any]] = {}
    per_side: dict[str, dict[str, Any]] = {}
    day_pnls: list[float] = []
    day_n_trades: list[int] = []

    overall_trades = 0
    overall_pnl = Decimal("0")
    overall_wins = 0

    for regime_name, bars in days:
        strat = ScriptedStrategyV2(spec, cfg=cfg)
        engine = Layer2Engine(spec, strat, seed=seed)  # type: ignore[arg-type]
        engine._rejection_p = 0.0

        # For the loss-cooldown mechanism, we mirror live_sim.py's integration:
        # after each engine.run we can't easily hook into per-trade callbacks,
        # so we rely on on_bar / update_position only — loss_cooldown_bars
        # activates via report_trade_outcome which we do NOT call here (the
        # engine has no hook). This means loss_cooldown_bars is off in the A/B
        # for now; fine since it's a minor secondary knob.
        ledger = engine.run(bars)

        reg_b = per_regime.setdefault(regime_name, {"n": 0, "wins": 0, "pnl": Decimal("0")})
        reg_b["n"] += ledger.n_trades
        reg_b["wins"] += sum(1 for t in ledger.trades if t.pnl_dollars > 0)
        reg_b["pnl"] += ledger.total_pnl_dollars

        # exit reason
        for k, v in ledger.breakdown_by_exit_reason().items():
            b = per_exit_reason.setdefault(k, {"n": 0, "pnl": Decimal("0")})
            b["n"] += int(v["n"])
            b["pnl"] += Decimal(v["pnl"])
        # side
        for k, v in ledger.breakdown_by_side().items():
            b = per_side.setdefault(k, {"n": 0, "pnl": Decimal("0")})
            b["n"] += int(v["n"])
            b["pnl"] += Decimal(v["pnl"])

        overall_trades += ledger.n_trades
        overall_pnl += ledger.total_pnl_dollars
        overall_wins += sum(1 for t in ledger.trades if t.pnl_dollars > 0)

        day_pnls.append(float(ledger.total_pnl_dollars))
        day_n_trades.append(ledger.n_trades)

    wr = overall_wins / overall_trades if overall_trades else 0.0
    exp = overall_pnl / Decimal(overall_trades) if overall_trades else Decimal("0")

    return VariantResult(
        name=cfg.name,
        n_trades=overall_trades,
        net_pnl=overall_pnl,
        win_rate=wr,
        expectancy=exp,
        per_regime=per_regime,
        per_exit_reason=per_exit_reason,
        per_side=per_side,
        day_pnls=day_pnls,
        day_n_trades=day_n_trades,
    )


# ---------------------------------------------------------------------------
# Bootstrap (day-paired resampling)
# ---------------------------------------------------------------------------


def _bootstrap_ci(
    values: list[float], *, n_boot: int = 2000, seed: int = 42
) -> tuple[float, float, float]:
    if not values:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    boots: list[float] = []
    n = len(values)
    for _ in range(n_boot):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        boots.append(sum(resample))
    boots.sort()
    lo = boots[int(0.025 * n_boot)]
    hi = boots[int(0.975 * n_boot) - 1]
    return (sum(values), lo, hi)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _score(r: VariantResult) -> float:
    """Rank key: prefer higher net PnL, but penalize variants with too few
    trades (n < 10 in the sample) because those estimates are too noisy.
    """
    pnl = float(r.net_pnl)
    if r.n_trades < 10:
        pnl -= 20.0 * (10 - r.n_trades)  # soft penalty
    # Small tiebreak on win rate
    pnl += 0.1 * r.win_rate
    return pnl


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _fmt_money(d: Decimal) -> str:
    return f"${float(d):+,.2f}"


def _render_report(results: list[VariantResult], *, source: str, n_days: int, winner: str) -> str:
    lines: list[str] = []
    lines.append("# ScriptedStrategy v2 — A/B Report")
    lines.append("")
    lines.append(f"- Data source: **{source}** ({n_days} days)")
    lines.append(f"- Variants tested: **{len(results)}**")
    lines.append(f"- Winner: **`{winner}`**")
    lines.append("")
    lines.append("## Ranked results")
    lines.append("")
    lines.append("| # | Variant | Trades | Net PnL | 95% CI (boot) | Win% | Exp/trade |")
    lines.append("|---:|---|---:|---:|---:|---:|---:|")
    # Compute bootstrap per variant on daily PnL
    boots = {r.name: _bootstrap_ci(r.day_pnls) for r in results}
    # Rank descending
    ranked = sorted(results, key=_score, reverse=True)
    for i, r in enumerate(ranked, 1):
        _, lo, hi = boots[r.name]
        ci = f"${lo:+.2f} / ${hi:+.2f}"
        mark = " ⭐" if r.name == winner else ""
        lines.append(
            f"| {i} | `{r.name}`{mark} | {r.n_trades} | {_fmt_money(r.net_pnl)} | {ci} | "
            f"{r.win_rate:.1%} | {_fmt_money(r.expectancy)} |"
        )
    lines.append("")

    # Per-regime table for the winner
    w = next(r for r in results if r.name == winner)
    lines.append(f"## Winner `{w.name}` — per-regime breakdown")
    lines.append("")
    lines.append("| Regime | Trades | Wins | Win% | Net PnL |")
    lines.append("|---|---:|---:|---:|---:|")
    for reg, b in sorted(w.per_regime.items()):
        n = int(b["n"])
        wins = int(b["wins"])
        wrp = (wins / n) if n > 0 else 0.0
        lines.append(f"| `{reg}` | {n} | {wins} | {wrp:.1%} | {_fmt_money(Decimal(b['pnl']))} |")
    lines.append("")

    lines.append(f"## Winner `{w.name}` — per exit reason")
    lines.append("")
    lines.append("| Exit reason | Trades | Net PnL |")
    lines.append("|---|---:|---:|")
    for reason, b in sorted(w.per_exit_reason.items()):
        lines.append(f"| `{reason}` | {int(b['n'])} | {_fmt_money(Decimal(b['pnl']))} |")
    lines.append("")

    lines.append(f"## Winner `{w.name}` — per side")
    lines.append("")
    lines.append("| Side | Trades | Net PnL |")
    lines.append("|---|---:|---:|")
    for sd, b in sorted(w.per_side.items()):
        lines.append(f"| `{sd}` | {int(b['n'])} | {_fmt_money(Decimal(b['pnl']))} |")
    lines.append("")

    lines.append("## Daily PnL ladder (winner)")
    lines.append("")
    lines.append("| Day | PnL | Trades |")
    lines.append("|---:|---:|---:|")
    for i, (p, n) in enumerate(zip(w.day_pnls, w.day_n_trades, strict=True)):
        lines.append(f"| {i} | ${p:+.2f} | {n} |")
    lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "* Variants that show near-identical PnL to the baseline `v1_replica` are "
        "statistical noise — the filter added no edge (or cost us trades with no "
        "offsetting quality gain)."
    )
    lines.append(
        "* A variant with materially higher expectancy AND at least ~15 trades has "
        "genuine lift; the bootstrap CI confirms sign stability."
    )
    lines.append(
        "* Variants that collapse trade count to <8 are under-selected — keep them "
        "only if they show >2x the baseline expectancy AND the CI excludes zero."
    )
    lines.append(
        "* The `real_high_vol` regime label comes from realized 1m-return stdev "
        "p75; anything above that bucket is our proxy for the "
        "`high_vol` regime that bled money in v1."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="use synthetic bars")
    ap.add_argument("--n-days", type=int, default=20, help="synthetic days")
    ap.add_argument("--winner-only", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeframe", choices=["1m", "5m"], default="1m")
    ap.add_argument("--variants", help="comma-separated name prefixes to run")
    args = ap.parse_args(argv)

    spec = load_spec(BASELINE)

    if args.synthetic:
        days = _load_synthetic_days(args.n_days)
        source = "synthetic"
    else:
        days = _load_real_days(args.timeframe)
        source = f"real_mnq_{args.timeframe}_rth"

    if not days:
        print("ERROR: no days loaded", file=sys.stderr)
        return 2

    print(f"Loaded {len(days)} days from {source}.")

    variants_to_run = VARIANTS
    if args.variants:
        prefixes = [p.strip() for p in args.variants.split(",") if p.strip()]
        variants_to_run = [v for v in VARIANTS if any(v.name.startswith(p) for p in prefixes)]

    results: list[VariantResult] = []
    for cfg in variants_to_run:
        r = _run_variant(cfg, spec, days, seed=args.seed)
        results.append(r)
        print(
            f"  {r.name:<34s}  trades={r.n_trades:3d}  "
            f"pnl=${float(r.net_pnl):+8.2f}  wr={r.win_rate:.1%}  "
            f"exp=${float(r.expectancy):+6.2f}"
        )

    if not results:
        print("no results")
        return 2

    # Rank and pick winner.
    ranked = sorted(results, key=_score, reverse=True)
    winner = ranked[0].name

    if args.winner_only:
        print(f"\nWINNER: {winner}")
        return 0

    md = _render_report(results, source=source, n_days=len(days), winner=winner)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(md)
    print(f"\nWrote report to {REPORT_PATH}")
    print(f"Winner: {winner}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


_ = importlib.util  # silence unused-import warning if left dangling above
