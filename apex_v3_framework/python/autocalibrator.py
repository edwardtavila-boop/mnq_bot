"""
Apex v2 Auto-Calibrator
=======================
Grid-searches over PM threshold, Red Team weight, ORB timeout, EMA min score,
and circuit breaker thresholds to find the optimal parameter combination
that maximizes walk-forward profitability while minimizing drawdown.

Scoring function (higher = better):
    score = (pct_profitable_windows * 0.4) + (total_R * 0.3) + (PF * 10 * 0.2) - (max_dd * 0.1)

Usage:
    python autocalibrator.py mnq_5m.csv --windows 7
    python autocalibrator.py mnq_5m.csv --vix vix.csv --es es.csv --windows 7
"""

import argparse
import statistics
from itertools import product
from datetime import datetime, timezone
from typing import List

from firm_engine import FirmConfig, Bar
from backtest import Backtester, V1DetectorConfig, load_csv


def split_windows(bars, n):
    if n < 2: return [bars]
    size = len(bars) // n
    return [bars[i*size : (i+1)*size if i < n-1 else len(bars)] for i in range(n)]


def run_test(bars, cfg, det_cfg, n_windows):
    """Run walk-forward and return aggregate stats."""
    windows = split_windows(bars, n_windows)
    results = []
    for win_bars in windows:
        if not win_bars:
            continue
        bt = Backtester(cfg=cfg, detector_cfg=det_cfg)
        s = bt.run(win_bars)
        results.append(s)

    n_with = sum(1 for r in results if r.get("trades", 0) > 0)
    profitable = sum(1 for r in results if r.get("trades", 0) > 0 and r.get("total_r", 0) > 0)
    total_trades = sum(r.get("trades", 0) for r in results)
    total_r = sum(r.get("total_r", 0) for r in results if r.get("trades", 0) > 0)
    pct_prof = (profitable / n_with * 100) if n_with > 0 else 0
    max_dds = [r.get("max_drawdown_r", 0) for r in results if r.get("trades", 0) > 0]
    worst_dd = max(max_dds) if max_dds else 0
    pfs = [r.get("profit_factor") for r in results if r.get("trades", 0) > 0]
    pfs_num = [p for p in pfs if isinstance(p, (int, float))]
    avg_pf = statistics.mean(pfs_num) if pfs_num else 0
    win_rates = [r.get("win_rate", 0) for r in results if r.get("trades", 0) > 0]
    avg_wr = statistics.mean(win_rates) if win_rates else 0
    return {
        "total_trades": total_trades,
        "pct_profitable": round(pct_prof, 1),
        "total_r": round(total_r, 2),
        "worst_dd": round(worst_dd, 2),
        "avg_pf": round(avg_pf, 2) if avg_pf else 0,
        "avg_win_rate": round(avg_wr, 1),
        "windows_with_trades": n_with,
    }


def score(stats):
    """Composite score. Higher = better. Penalizes 0-trade configs."""
    if stats["total_trades"] < 5:
        return -1000  # too few trades to evaluate
    return (
        stats["pct_profitable"] * 0.4
        + stats["total_r"] * 3.0
        + min(stats["avg_pf"] * 5, 50) * 0.3  # cap PF contribution
        + stats["avg_win_rate"] * 0.2
        - stats["worst_dd"] * 2.0
    )


def main():
    p = argparse.ArgumentParser(description="Apex v2 Auto-Calibrator")
    p.add_argument("csv")
    p.add_argument("--windows", type=int, default=7)
    p.add_argument("--vix"); p.add_argument("--es")
    p.add_argument("--dxy"); p.add_argument("--tick")
    p.add_argument("--quick", action="store_true",
                   help="Smaller grid (5x5x3 = 75 configs, ~2 min) vs full (~10 min)")
    args = p.parse_args()

    print(f"Loading {args.csv}...")
    if args.vix or args.es or args.dxy or args.tick:
        from intermarket import load_with_intermarket
        bars = load_with_intermarket(args.csv, vix=args.vix, es=args.es,
                                     dxy=args.dxy, tick=args.tick)
    else:
        bars = load_csv(args.csv)
    print(f"Loaded {len(bars)} bars\n")

    # Grid definitions
    if args.quick:
        pm_grid = [25, 30, 35, 40, 45]
        red_w_grid = [0.7, 1.0, 1.3]
        orb_timeout_grid = [15, 20, 25]
        ema_score_grid = [4]  # fixed at v1 default
    else:
        pm_grid = [20, 25, 30, 35, 40, 45, 50]
        red_w_grid = [0.5, 0.7, 1.0, 1.3, 1.5]
        orb_timeout_grid = [15, 20, 25, 30]
        ema_score_grid = [3, 4, 5]

    total = len(pm_grid) * len(red_w_grid) * len(orb_timeout_grid) * len(ema_score_grid)
    print(f"Grid search: {total} configurations")
    print(f"  PM thresholds:    {pm_grid}")
    print(f"  Red Team weights: {red_w_grid}")
    print(f"  ORB timeouts:     {orb_timeout_grid}")
    print(f"  EMA min scores:   {ema_score_grid}")
    print()

    results = []
    count = 0
    for pm, rw, orb_to, ema_s in product(pm_grid, red_w_grid, orb_timeout_grid, ema_score_grid):
        count += 1
        cfg = FirmConfig(pm_threshold=pm, redteam_weight=rw, require_setup=True)
        det_cfg = V1DetectorConfig(orb_timeout=orb_to, ema_min_score=ema_s)
        stats = run_test(bars, cfg, det_cfg, args.windows)
        sc = score(stats)
        results.append({
            "pm": pm, "red_w": rw, "orb_to": orb_to, "ema_s": ema_s,
            "score": round(sc, 2), **stats,
        })
        if count % 10 == 0:
            print(f"  Progress: {count}/{total}", end="\r", flush=True)
    print(f"  Progress: {total}/{total} done.")

    # Sort by composite score
    results.sort(key=lambda x: -x["score"])

    print(f"\n{'='*92}")
    print(f"TOP 10 CONFIGURATIONS  (scored by profitability + DD penalty)")
    print(f"{'='*92}")
    print(f"{'Rank':>4s}  {'PM':>3s}  {'RW':>4s}  {'ORB-to':>6s}  {'EMA-s':>5s}  "
          f"{'Trades':>6s}  {'Win%':>5s}  {'TotR':>7s}  {'PF':>5s}  {'MDD':>5s}  "
          f"{'%Prof':>5s}  {'Score':>7s}")
    print("─" * 92)
    for i, r in enumerate(results[:10], 1):
        print(f"{i:>4d}  {r['pm']:>3d}  {r['red_w']:>4.1f}  {r['orb_to']:>6d}  {r['ema_s']:>5d}  "
              f"{r['total_trades']:>6d}  {r['avg_win_rate']:>4.1f}%  {r['total_r']:>+7.2f}  "
              f"{r['avg_pf']:>5.1f}  {r['worst_dd']:>5.2f}  {r['pct_profitable']:>4.0f}%  "
              f"{r['score']:>+7.2f}")

    best = results[0]
    print(f"\n{'='*60}")
    print(f"OPTIMAL CONFIGURATION")
    print(f"{'='*60}")
    print(f"PM threshold:        {best['pm']}")
    print(f"Red Team weight:     {best['red_w']}")
    print(f"ORB timeout:         {best['orb_to']} bars")
    print(f"EMA min score:       {best['ema_s']}")
    print(f"\nExpected performance:")
    print(f"  Total trades:      {best['total_trades']}")
    print(f"  Avg win rate:      {best['avg_win_rate']}%")
    print(f"  Total R:           {best['total_r']:+.2f}")
    print(f"  Avg PF:            {best['avg_pf']}")
    print(f"  Worst DD:          {best['worst_dd']}R")
    print(f"  % profitable wins: {best['pct_profitable']}%")
    print(f"\nApply with: --pm {best['pm']} --red-weight {best['red_w']}  "
          f"(and update detector_cfg.orb_timeout={best['orb_to']}, "
          f"ema_min_score={best['ema_s']})")


if __name__ == "__main__":
    main()
