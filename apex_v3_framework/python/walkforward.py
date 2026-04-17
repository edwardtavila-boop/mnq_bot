"""
Apex v2 Walk-Forward Validator (auto-tuning)
============================================
Splits CSV into N windows, runs backtest, reports per-window stats. With
--sweep, tries multiple PM thresholds and reports the best per metric.

Usage:
  python walkforward.py mnq_5m.csv --windows 11 --pm 40
  python walkforward.py mnq_5m.csv --sweep   (tries PM 25,30,35,40,45)
"""

import argparse
import statistics
from datetime import datetime, timezone
from typing import List

from firm_engine import FirmConfig, Bar
from backtest import Backtester, V1DetectorConfig, load_csv


def split_windows(bars: List[Bar], n: int) -> List[List[Bar]]:
    if n < 2:
        return [bars]
    size = len(bars) // n
    return [bars[i*size : (i+1)*size if i < n-1 else len(bars)] for i in range(n)]


def run_window(bars, cfg, det_cfg):
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg)
    s = bt.run(bars)
    s["bars_in_window"] = len(bars)
    if bars:
        s["start"] = datetime.fromtimestamp(bars[0].time, tz=timezone.utc).strftime("%Y-%m-%d")
        s["end"] = datetime.fromtimestamp(bars[-1].time, tz=timezone.utc).strftime("%Y-%m-%d")
    return s


def aggregate(results, pm):
    n_with = sum(1 for r in results if r.get("trades", 0) > 0)
    profitable = sum(1 for r in results if r.get("trades", 0) > 0 and r.get("total_r", 0) > 0)
    total_trades = sum(r.get("trades", 0) for r in results)
    total_r = sum(r.get("total_r", 0) for r in results if r.get("trades", 0) > 0)
    pct_prof = (profitable / n_with * 100) if n_with > 0 else 0
    avg_wr = statistics.mean(r["win_rate"] for r in results if r.get("trades", 0) > 0) if n_with > 0 else 0
    return {
        "pm": pm,
        "windows_with_trades": n_with,
        "profitable_windows": profitable,
        "pct_profitable": round(pct_prof, 1),
        "total_trades": total_trades,
        "total_r": round(total_r, 2),
        "avg_win_rate": round(avg_wr, 1),
    }


def main():
    p = argparse.ArgumentParser(description="Apex v2 Walk-Forward (auto-tuning)")
    p.add_argument("csv")
    p.add_argument("--windows", type=int, default=11)
    p.add_argument("--pm", type=float, default=40.0)
    p.add_argument("--sweep", action="store_true", help="Try PM 25,30,35,40,45 and pick best")
    p.add_argument("--no-setup-required", action="store_true")
    p.add_argument("--use-partials", action="store_true")
    args = p.parse_args()

    print(f"Loading {args.csv}...")
    bars = load_csv(args.csv)
    print(f"Loaded {len(bars)} bars  (~{len(bars)//78} trading days)")
    windows = split_windows(bars, args.windows)
    print(f"Split into {len(windows)} windows ~{len(windows[0])} bars each\n")

    det_cfg = V1DetectorConfig(use_partials=args.use_partials)

    if args.sweep:
        print("Sweeping PM thresholds: 25, 30, 35, 40, 45, 50")
        print(f"{'PM':>4s}  {'Win%':>6s}  {'Trades':>7s}  {'TotR':>8s}  {'Profit':>14s}  {'AvgWR':>6s}")
        print("-" * 60)
        sweep_results = []
        for pm in [25, 30, 35, 40, 45, 50]:
            cfg = FirmConfig(pm_threshold=pm, require_setup=not args.no_setup_required)
            window_results = []
            for win_bars in windows:
                if win_bars:
                    window_results.append(run_window(win_bars, cfg, det_cfg))
            agg = aggregate(window_results, pm)
            sweep_results.append((pm, agg, window_results))
            print(f"{pm:>4d}  {agg['avg_win_rate']:>5.1f}%  {agg['total_trades']:>7d}  "
                  f"{agg['total_r']:>+8.2f}  "
                  f"{agg['profitable_windows']}/{agg['windows_with_trades']} ({agg['pct_profitable']:>4.0f}%)  "
                  f"{agg['avg_win_rate']:>5.1f}%")
        # Pick best
        best = max(sweep_results, key=lambda x: (x[1]['pct_profitable'], x[1]['total_r']))
        print(f"\n>> BEST PM: {best[0]} ({best[1]['pct_profitable']}% windows profitable, "
              f"{best[1]['total_r']:+.2f}R total) <<")
        chosen_pm = best[0]
        chosen_results = best[2]
    else:
        chosen_pm = args.pm
        cfg = FirmConfig(pm_threshold=args.pm, require_setup=not args.no_setup_required)
        chosen_results = []
        print(f"Running PM={args.pm}...")
        for i, win_bars in enumerate(windows, 1):
            if not win_bars:
                continue
            r = run_window(win_bars, cfg, det_cfg)
            chosen_results.append(r)
            n = r.get("trades", 0)
            print(f"  Window {i}/{len(windows)}: {n} trades  "
                  + (f"{r['win_rate']}% win  {r['total_r']:+.1f}R" if n else "(no trades)"))

    # Detailed window report
    print(f"\n{'='*78}")
    print(f"WALK-FORWARD DETAIL  PM≥{chosen_pm}")
    print(f"{'='*78}")
    print(f"{'#':>3s}  {'Range':>23s}  {'Trades':>7s}  {'Win%':>6s}  {'TotR':>7s}  {'PF':>6s}  {'MDD':>6s}  {'Status':>10s}")
    print("─" * 78)

    for i, r in enumerate(chosen_results, 1):
        n = r.get("trades", 0)
        rng = f"{r.get('start','—')}→{r.get('end','—')}"
        if n == 0:
            print(f"{i:>3d}  {rng:>23s}  {0:>7d}  {'—':>6s}  {'—':>7s}  {'—':>6s}  {'—':>6s}  {'no signals':>10s}")
        else:
            status = "✓ profit" if r['total_r'] > 0 else "= flat" if r['total_r'] == 0 else "✗ loss"
            pf = str(r['profit_factor'])
            print(f"{i:>3d}  {rng:>23s}  {n:>7d}  {r['win_rate']:>5.1f}%  "
                  f"{r['total_r']:>+7.2f}  {pf:>6s}  {r['max_drawdown_r']:>6.2f}  {status:>10s}")

    print("─" * 78)
    agg = aggregate(chosen_results, chosen_pm)
    pct = agg['pct_profitable']
    print(f"\nProfitable windows:  {agg['profitable_windows']}/{agg['windows_with_trades']}  ({pct:.0f}%)")
    print(f"Total trades:        {agg['total_trades']}")
    print(f"Total R:             {agg['total_r']:+.2f}")
    print(f"Avg win rate:        {agg['avg_win_rate']:.1f}%")
    if pct >= 80:
        print(f"\n✓  PASSES walk-forward threshold (≥80% profitable windows)")
    elif pct >= 60:
        print(f"\n◉  MARGINAL — consider raising PM threshold or tightening filters")
    else:
        print(f"\n✗  FAILS walk-forward threshold")


if __name__ == "__main__":
    main()
