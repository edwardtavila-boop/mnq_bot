"""
Apex v2 Trade Execution Analyzer
================================
Runs a backtest then analyzes every trade bar-by-bar to extract execution
intelligence:

  - MAE distribution per setup (how deep did winners pull back?)
  - MFE distribution per setup (how far did winners run?)
  - Optimal hold time per setup (when does MFE peak on average?)
  - MFE capture ratio (how much of peak profit did we keep?)
  - Pullback opportunity (could we have entered better with a limit order?)

Output: actionable guidance for tuning entry/exit logic.

Usage:
    python execution_analyzer.py mnq_5m.csv --pm 30
    python execution_analyzer.py mnq_5m.csv --vix vix.csv --es es.csv --pm 30
"""

import argparse
from collections import defaultdict
from datetime import UTC, datetime

from backtest import Backtester, V1DetectorConfig, load_csv
from firm_engine import FirmConfig


def percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def analyze(trades):
    """Per-setup execution analysis."""
    by_setup = defaultdict(list)
    for t in trades:
        by_setup[t.setup].append(t)

    report = {}
    for setup, ts in by_setup.items():
        winners = [t for t in ts if t.pnl_r > 0]
        losers = [t for t in ts if t.pnl_r < 0]

        # MAE on winners (how much pain before profit)
        winner_mae = [abs(t.mae_R) for t in winners]
        # MFE on losers (did losers ever go in our favor first?)
        loser_mfe = [t.mfe_R for t in losers]
        # MFE on winners (how far did we run?)
        winner_mfe = [t.mfe_R for t in winners]
        # MFE-to-exit-bar (how long until MFE peaks?)
        winner_mfe_bars = [t.mfe_bar for t in winners]
        # MFE capture ratio: realized R / peak MFE
        capture_ratios = [t.pnl_r / t.mfe_R for t in winners if t.mfe_R > 0]

        # Pullback opportunity: did the trade ever pull back > 0.2R after entry
        # before going to TP1? (= signal we could have entered later for better R)
        pullback_avail = [
            abs(t.mae_R) > 0.2 for t in ts if t.outcome in ("tp1", "tp2", "tp1_then_be")
        ]
        pullback_pct = sum(pullback_avail) / len(pullback_avail) * 100 if pullback_avail else 0

        report[setup] = {
            "trades": len(ts),
            "winners": len(winners),
            "losers": len(losers),
            "winner_mae_p50": round(percentile(winner_mae, 50), 2),
            "winner_mae_p90": round(percentile(winner_mae, 90), 2),
            "loser_mfe_p50": round(percentile(loser_mfe, 50), 2),
            "loser_mfe_p90": round(percentile(loser_mfe, 90), 2),
            "winner_mfe_p50": round(percentile(winner_mfe, 50), 2),
            "winner_mfe_p90": round(percentile(winner_mfe, 90), 2),
            "winner_mfe_bars_p50": int(percentile(winner_mfe_bars, 50)),
            "winner_mfe_bars_p90": int(percentile(winner_mfe_bars, 90)),
            "capture_ratio_p50": round(percentile(capture_ratios, 50) * 100, 1)
            if capture_ratios
            else 0,
            "pullback_avail_pct": round(pullback_pct, 1),
        }
    return report


def print_report(report):
    print("\n" + "=" * 90)
    print("EXECUTION ANALYSIS — what the data says about your entries and exits")
    print("=" * 90)
    print(
        f"{'Setup':>8s}  {'Trades':>6s}  {'WinMAE':>7s}  {'LosrMFE':>7s}  "
        f"{'WinMFE':>7s}  {'MFE@bar':>8s}  {'Capture%':>9s}  {'Pullback':>9s}"
    )
    print("─" * 90)
    print(
        f"{'':>8s}  {'':>6s}  {'p50/p90':>7s}  {'p50/p90':>7s}  "
        f"{'p50/p90':>7s}  {'p50/p90':>8s}  {'realized':>9s}  {'avail %':>9s}"
    )
    print("─" * 90)
    for setup, r in report.items():
        print(
            f"{setup:>8s}  {r['trades']:>6d}  "
            f"{r['winner_mae_p50']:>3.2f}/{r['winner_mae_p90']:>2.1f}  "
            f"{r['loser_mfe_p50']:>3.2f}/{r['loser_mfe_p90']:>2.1f}  "
            f"{r['winner_mfe_p50']:>3.2f}/{r['winner_mfe_p90']:>2.1f}  "
            f"{r['winner_mfe_bars_p50']:>3d}/{r['winner_mfe_bars_p90']:>4d}  "
            f"{r['capture_ratio_p50']:>8.1f}%  "
            f"{r['pullback_avail_pct']:>8.1f}%"
        )

    print("\nLegend:")
    print(
        "  WinMAE   = Maximum Adverse Excursion on winners (how much they pulled back before going)"
    )
    print("  LosrMFE  = Maximum Favorable Excursion on losers (did losers go in our favor first)")
    print("  WinMFE   = Peak unrealized profit on winners (in R units)")
    print("  MFE@bar  = Number of bars from entry to peak MFE")
    print("  Capture% = realized R / peak MFE (higher = better exit)")
    print("  Pullback = % of resolved trades that pulled back >0.2R (entry opportunity)")


def actionable(report):
    print("\n" + "=" * 90)
    print("ACTIONABLE GUIDANCE")
    print("=" * 90)
    for setup, r in report.items():
        if r["trades"] < 2:
            continue
        print(f"\n{setup}:")
        # Entry quality check
        if r["winner_mae_p50"] < 0.2:
            print(
                f"  ✓ ENTRY QUALITY HIGH: median winner pulled back only {r['winner_mae_p50']}R "
                f"after entry. Current entry timing is solid."
            )
        elif r["winner_mae_p50"] > 0.5:
            print(
                f"  ⚠ ENTRY TIMING: median winner pulled back {r['winner_mae_p50']}R before going. "
                f"A pullback-limit entry at signal_close − {r['winner_mae_p50'] * 0.6:.2f}R would have "
                f"caught most of these at better prices."
            )
        # Exit quality check
        if r["capture_ratio_p50"] < 50 and r["winner_mfe_p50"] > 1.5:
            print(
                f"  ⚠ EXIT TIMING: leaving {100 - r['capture_ratio_p50']:.0f}% of profit on the table. "
                f"Median peak was {r['winner_mfe_p50']}R but we captured only "
                f"{r['winner_mfe_p50'] * r['capture_ratio_p50'] / 100:.2f}R. Consider extending TP2."
            )
        elif r["capture_ratio_p50"] > 75:
            print(
                f"  ✓ EXIT QUALITY: capturing {r['capture_ratio_p50']:.0f}% of peak MFE. Tight exits."
            )
        # Hold time guidance
        if r["winner_mfe_bars_p50"] > 0:
            print(
                f"  → OPTIMAL HOLD: median MFE peaks at bar {r['winner_mfe_bars_p50']} "
                f"(p90: bar {r['winner_mfe_bars_p90']}). Consider timeout = "
                f"{int(r['winner_mfe_bars_p90'] * 1.2)} bars for this setup."
            )
        # Loser recovery check
        if r["loser_mfe_p50"] > 0.5:
            print(
                f"  ⚠ LOSERS HAD CHANCES: median losing trade reached +{r['loser_mfe_p50']}R "
                f"unrealized before failing. A trail-after-{r['loser_mfe_p50'] * 0.7:.1f}R rule "
                f"would have salvaged some of these."
            )
        # Pullback opportunity
        if r["pullback_avail_pct"] > 60:
            print(
                f"  → PULLBACK ENTRIES: {r['pullback_avail_pct']:.0f}% of winners pulled back "
                f"after entry. Pullback-limit entry mode is highly recommended for this setup."
            )


def main():
    p = argparse.ArgumentParser(description="Apex v2 Trade Execution Analyzer")
    p.add_argument("csv")
    p.add_argument("--pm", type=float, default=30.0)
    p.add_argument("--vix")
    p.add_argument("--es")
    p.add_argument("--dxy")
    p.add_argument("--tick")
    args = p.parse_args()

    print(f"Loading {args.csv}...")
    if any([args.vix, args.es, args.dxy, args.tick]):
        from intermarket import load_with_intermarket

        bars = load_with_intermarket(
            args.csv, vix=args.vix, es=args.es, dxy=args.dxy, tick=args.tick
        )
    else:
        bars = load_csv(args.csv)
    print(f"Loaded {len(bars)} bars\n")

    cfg = FirmConfig(pm_threshold=args.pm, require_setup=True)
    bt = Backtester(cfg=cfg, detector_cfg=V1DetectorConfig())
    s = bt.run(bars)
    print(
        f"Backtest complete: {s.get('trades', 0)} trades, "
        f"{s.get('total_r', 0)}R total, PF {s.get('profit_factor', '—')}"
    )

    if not bt.trades:
        print("No trades to analyze.")
        return

    report = analyze(bt.trades)
    print_report(report)
    actionable(report)

    # Show all trades with execution detail
    print("\n" + "=" * 90)
    print("TRADE-BY-TRADE EXECUTION DETAIL")
    print("=" * 90)
    print(
        f"{'Time':>16s}  {'Setup':>6s}  {'Side':>5s}  {'Bars':>4s}  "
        f"{'MAE':>6s}  {'MFE':>6s}  {'@Bar':>5s}  {'PnL':>6s}  {'Outcome':>14s}"
    )
    print("─" * 90)
    for t in bt.trades:
        dt = datetime.fromtimestamp(t.open_time, tz=UTC).strftime("%m-%d %H:%M")
        (t.pnl_r / t.mfe_R * 100) if t.mfe_R > 0 else 0
        print(
            f"{dt:>16s}  {t.setup:>6s}  {t.side:>5s}  {t.bars_to_resolution:>4d}  "
            f"{t.mae_R:>+5.2f}R  {t.mfe_R:>+5.2f}R  {t.mfe_bar:>5d}  "
            f"{t.pnl_r:>+5.2f}R  {t.outcome:>14s}"
        )


if __name__ == "__main__":
    main()
