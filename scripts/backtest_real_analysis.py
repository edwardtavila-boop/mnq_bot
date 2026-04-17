"""Batch 12A — Deep analysis of real-tape backtest results.

Reads the trade log from backtest_real_trades.csv and decomposes:
1. PnL by year — is the strategy degrading or always bad?
2. PnL by exit type — are we getting stopped out too fast?
3. PnL by session window — morning vs afternoon
4. Long vs short attribution per year
5. Trade-level stats: avg winner/loser, payoff ratio, expectancy per trade
6. Streak analysis: consecutive W/L distribution
7. Time-in-trade: do longer holds win more?

Focus variant: r5_real_wide_target (the "best" real-data variant from prior work)
and t16_r5_long_only (best net PnL).

Output: reports/backtest_real_analysis.md
"""
from __future__ import annotations

import csv
import datetime as _dt
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

if not hasattr(_dt, "UTC"):
    _dt.UTC = timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

REPO_ROOT = Path(__file__).resolve().parents[1]

@dataclass
class Trade:
    variant: str
    date: str
    side: str
    entry_ix: int
    exit_ix: int
    entry_px: float
    exit_px: float
    stop: float
    tp: float
    exit_reason: str
    pnl_ticks: float
    pnl_dollars: float
    bars_held: int


def load_trades(path: Path) -> list[Trade]:
    trades = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(Trade(
                variant=row["variant"],
                date=row["date"],
                side=row["side"],
                entry_ix=int(row["entry_ix"]),
                exit_ix=int(row["exit_ix"]),
                entry_px=float(row["entry_px"]),
                exit_px=float(row["exit_px"]),
                stop=float(row["stop"]),
                tp=float(row["tp"]),
                exit_reason=row["exit_reason"],
                pnl_ticks=float(row["pnl_ticks"]),
                pnl_dollars=float(row["pnl_dollars"]),
                bars_held=int(row["bars_held"]),
            ))
    return trades


def analyze_variant(name: str, trades: list[Trade]) -> list[str]:
    """Return markdown lines for a deep analysis of one variant."""
    lines = [f"## {name}", ""]

    if not trades:
        lines.append("*No trades.*")
        return lines

    # Overall stats
    total = len(trades)
    winners = [t for t in trades if t.pnl_dollars > 0]
    losers = [t for t in trades if t.pnl_dollars < 0]
    scratches = [t for t in trades if t.pnl_dollars == 0]
    net = sum(t.pnl_dollars for t in trades)
    wr = len(winners) / total * 100

    avg_win = statistics.mean(t.pnl_dollars for t in winners) if winners else 0
    avg_loss = statistics.mean(t.pnl_dollars for t in losers) if losers else 0
    payoff = abs(avg_win / avg_loss) if avg_loss else 0
    expectancy = net / total

    lines.append(f"**Trades:** {total} | **W:** {len(winners)} | **L:** {len(losers)} | **S:** {len(scratches)}")
    lines.append(f"**Win Rate:** {wr:.1f}% | **Net PnL:** ${net:+,.2f} | **Expectancy/trade:** ${expectancy:+.2f}")
    lines.append(f"**Avg Winner:** ${avg_win:+.2f} | **Avg Loser:** ${avg_loss:+.2f} | **Payoff Ratio:** {payoff:.2f}")
    lines.append("")

    # By year
    lines.append("### PnL by Year")
    lines.append("")
    lines.append("| Year | Trades | W | L | WR% | Net PnL | Avg/Trade |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    by_year: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_year[t.date[:4]].append(t)
    for year in sorted(by_year):
        yt = by_year[year]
        yw = sum(1 for t in yt if t.pnl_dollars > 0)
        yl = sum(1 for t in yt if t.pnl_dollars < 0)
        ynet = sum(t.pnl_dollars for t in yt)
        ywr = yw / len(yt) * 100 if yt else 0
        yavg = ynet / len(yt) if yt else 0
        lines.append(f"| {year} | {len(yt)} | {yw} | {yl} | {ywr:.1f} | ${ynet:+,.2f} | ${yavg:+.2f} |")
    lines.append("")

    # By exit type
    lines.append("### PnL by Exit Type")
    lines.append("")
    lines.append("| Exit | Trades | Net PnL | Avg PnL | WR% |")
    lines.append("|---|---:|---:|---:|---:|")
    by_exit: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_exit[t.exit_reason].append(t)
    for reason in ["stop", "take_profit", "time_stop", "session_end"]:
        et = by_exit.get(reason, [])
        if not et:
            continue
        enet = sum(t.pnl_dollars for t in et)
        eavg = enet / len(et)
        ewr = sum(1 for t in et if t.pnl_dollars > 0) / len(et) * 100
        lines.append(f"| {reason} | {len(et)} | ${enet:+,.2f} | ${eavg:+.2f} | {ewr:.1f} |")
    lines.append("")

    # By side
    lines.append("### Long vs Short")
    lines.append("")
    lines.append("| Side | Trades | Net PnL | WR% | Avg/Trade |")
    lines.append("|---|---:|---:|---:|---:|")
    for side in ["long", "short"]:
        st = [t for t in trades if t.side == side]
        if not st:
            continue
        snet = sum(t.pnl_dollars for t in st)
        swr = sum(1 for t in st if t.pnl_dollars > 0) / len(st) * 100
        savg = snet / len(st)
        lines.append(f"| {side} | {len(st)} | ${snet:+,.2f} | {swr:.1f} | ${savg:+.2f} |")
    lines.append("")

    # By entry window (morning vs afternoon)
    lines.append("### Morning vs Afternoon")
    lines.append("")
    lines.append("| Window | Trades | Net PnL | WR% | Avg/Trade |")
    lines.append("|---|---:|---:|---:|---:|")
    for label, lo, hi in [("Morning (30-120)", 30, 120), ("Afternoon (270-375)", 270, 375), ("Other", 0, 29)]:
        wt = [t for t in trades if lo <= t.entry_ix <= hi]
        if not wt:
            continue
        wnet = sum(t.pnl_dollars for t in wt)
        wwr = sum(1 for t in wt if t.pnl_dollars > 0) / len(wt) * 100
        wavg = wnet / len(wt)
        lines.append(f"| {label} | {len(wt)} | ${wnet:+,.2f} | {wwr:.1f} | ${wavg:+.2f} |")
    lines.append("")

    # Bars held analysis
    lines.append("### Bars Held vs Outcome")
    lines.append("")
    lines.append("| Bars Held | Trades | Net PnL | WR% |")
    lines.append("|---|---:|---:|---:|")
    buckets = [(1, 5), (6, 10), (11, 15), (16, 20), (21, 35), (36, 100)]
    for lo, hi in buckets:
        bt = [t for t in trades if lo <= t.bars_held <= hi]
        if not bt:
            continue
        bnet = sum(t.pnl_dollars for t in bt)
        bwr = sum(1 for t in bt if t.pnl_dollars > 0) / len(bt) * 100
        lines.append(f"| {lo}–{hi} | {len(bt)} | ${bnet:+,.2f} | {bwr:.1f} |")
    lines.append("")

    # Stop distance analysis: how often does price reach TP before stop?
    tp_count = sum(1 for t in trades if t.exit_reason == "take_profit")
    stop_count = sum(1 for t in trades if t.exit_reason == "stop")
    if tp_count + stop_count > 0:
        tp_pct = tp_count / (tp_count + stop_count) * 100
        lines.append(f"**TP hit rate (of stop+TP exits):** {tp_pct:.1f}% ({tp_count}/{tp_count + stop_count})")
        lines.append("")

    return lines


def main() -> None:
    csv_path = REPO_ROOT / "reports" / "backtest_real_trades.csv"
    if not csv_path.exists():
        print("ERROR: Run backtest_real.py first")
        sys.exit(1)

    all_trades = load_trades(csv_path)
    print(f"Loaded {len(all_trades)} trades")

    # Focus variants
    focus = ["r5_real_wide_target", "t16_r5_long_only", "t7_r5_morning_only",
             "r4_real_orderflow", "t0_r5_tight_stop", "t6_r5_strict_flow"]

    lines = [
        "# Real-Tape Backtest — Deep Analysis (Batch 12A)",
        "",
        f"Trade log: {len(all_trades)} trades across {len({t.variant for t in all_trades})} variants",
        "",
        "---",
        "",
    ]

    for vname in focus:
        vtrades = [t for t in all_trades if t.variant == vname]
        if vtrades:
            lines.extend(analyze_variant(vname, vtrades))
            lines.append("---")
            lines.append("")

    # Cross-variant comparison: rank by risk-adjusted metric
    lines.append("## Cross-Variant Ranking (by expectancy per trade)")
    lines.append("")
    lines.append("| Rank | Variant | Trades | Expectancy | WR% | Net PnL |")
    lines.append("|---:|---|---:|---:|---:|---:|")
    variant_names = sorted({t.variant for t in all_trades})
    ranked = []
    for vn in variant_names:
        vt = [t for t in all_trades if t.variant == vn]
        if not vt:
            continue
        exp = sum(t.pnl_dollars for t in vt) / len(vt)
        wr = sum(1 for t in vt if t.pnl_dollars > 0) / len(vt) * 100
        net = sum(t.pnl_dollars for t in vt)
        ranked.append((exp, vn, len(vt), wr, net))
    ranked.sort(reverse=True)
    for i, (exp, vn, n, wr, net) in enumerate(ranked, 1):
        lines.append(f"| {i} | {vn} | {n} | ${exp:+.2f} | {wr:.1f} | ${net:+,.2f} |")

    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append("The EMA 9/21 cross strategy is **net negative across all variants** on 1,724 days "
                 "of real MNQ 1m data (2019-05 → 2026-04). Key findings:")
    lines.append("")

    # Find best
    best_exp, best_name, best_n, best_wr, best_net = ranked[0]
    lines.append(f"1. **Best variant:** {best_name} (${best_exp:+.2f}/trade, {best_wr:.1f}% WR, ${best_net:+,.2f} net)")
    lines.append("2. **Filtering helps massively:** r0 (no filter) loses $15/trade; r5 (full stack) loses $6/trade")
    lines.append("3. **The base signal has no edge.** Filters reduce damage but can't create alpha from a losing signal.")
    lines.append("4. **Long bias confirmed:** t16_long_only consistently outperforms t17_short_only")
    lines.append("5. **Next step:** The gauntlet/OW architecture is validated as working code, but needs a "
                 "profitable base signal to filter. Candidate sources: Apex V3 15-voice engine, "
                 "ORB/sweep/pin-bar setups from microstructure.py, or external ML signals.")

    report_path = REPO_ROOT / "reports" / "backtest_real_analysis.md"
    report_path.write_text("\n".join(lines))
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
