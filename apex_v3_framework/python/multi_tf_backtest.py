"""
Multi-Timeframe Backtest Runner
================================
Runs the Apex v2 Firm engine on different timeframes by auto-scaling
detector parameters. Each timeframe has its own optimal settings:

  5m:  Original - ORB 15min (3 bars), timeout 20 bars, cooldown 12 bars
  15m: ORB 45min (3 bars), timeout 12 bars, cooldown 4 bars
  1h:  ORB 2h (2 bars), timeout 8 bars, cooldown 2 bars

Usage:
  python multi_tf_backtest.py --tf 15m --csv nq_15m.csv --pm 25
  python multi_tf_backtest.py --tf 1h  --csv nq_1h.csv  --pm 30
"""

import argparse
from datetime import datetime, timezone
from firm_engine import FirmConfig, Bar
from backtest import Backtester, V1DetectorConfig, load_csv
from intermarket import load_with_intermarket


TF_PROFILES = {
    "5m": {
        "bars_per_hour": 12,
        "orb_window_bars": 3,    # 15 minutes of RTH open = 3 bars
        "orb_timeout": 20,       # ~1.5 hours to resolve
        "cooldown": 12,
        "swing_lb": 15,
        "sweep_bos_valid": 40,
        "ema_tod_filter": "Power Hours",
        "ema_dow_filter": "Skip Thursday",
    },
    "15m": {
        "bars_per_hour": 4,
        "orb_window_bars": 3,    # 45 minutes of RTH open
        "orb_timeout": 12,       # 3 hours to resolve
        "cooldown": 4,
        "swing_lb": 10,
        "sweep_bos_valid": 20,
        "ema_tod_filter": "Full Session",  # Less granular TOD filter
        "ema_dow_filter": "Skip Thursday",
    },
    "1h": {
        "bars_per_hour": 1,
        "orb_window_bars": 2,    # 2-hour OR (first 2 hours of RTH)
        "orb_timeout": 8,        # 8 hours to resolve
        "cooldown": 2,
        "swing_lb": 8,
        "sweep_bos_valid": 12,
        "ema_tod_filter": "Full Session",
        "ema_dow_filter": "All Days",
    },
}


def run_tf_backtest(csv_path, tf, pm, vix=None, es=None, dxy=None, tick=None,
                    verbose=False):
    """Run backtest on specified timeframe with scaled params."""
    if tf not in TF_PROFILES:
        raise ValueError(f"Unknown timeframe: {tf}. Use 5m, 15m, or 1h.")
    profile = TF_PROFILES[tf]

    # Load data (with intermarket if provided)
    if vix or es or dxy or tick:
        bars = load_with_intermarket(csv_path, vix=vix, es=es, dxy=dxy, tick=tick)
    else:
        bars = load_csv(csv_path)

    # Build detector config with TF-scaled params
    det_cfg = V1DetectorConfig(
        orb_timeout=profile["orb_timeout"],
        cooldown=profile["cooldown"],
        swing_lb=profile["swing_lb"],
        sweep_bos_valid=profile["sweep_bos_valid"],
        ema_tod_filter=profile["ema_tod_filter"],
        ema_dow_filter=profile["ema_dow_filter"],
    )
    cfg = FirmConfig(pm_threshold=pm, require_setup=True)
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg)
    s = bt.run(bars)

    if verbose:
        print(f"\n{'='*70}")
        print(f"{tf} BACKTEST · {len(bars)} bars · PM={pm}")
        print(f"{'='*70}")
        if bars:
            print(f"  Start: {datetime.fromtimestamp(bars[0].time, tz=timezone.utc).strftime('%Y-%m-%d')}")
            print(f"  End:   {datetime.fromtimestamp(bars[-1].time, tz=timezone.utc).strftime('%Y-%m-%d')}")
        if s.get('trades', 0) == 0:
            print(f"  No trades fired. Decisions: {s.get('decisions', 0)}")
            return s, bt
        print(f"  Trades:       {s['trades']}  (W:{s['wins']} L:{s['losses']} BE:{s.get('breakevens', 0)})")
        print(f"  Strike rate:  {s['wins']/(s['wins']+s['losses'])*100:.1f}%" if s['wins']+s['losses']>0 else "  Strike rate: —")
        print(f"  Total R:      {s['total_r']:+.2f}")
        print(f"  Profit factor:{s['profit_factor']}")
        print(f"  Max DD:       {s['max_drawdown_r']}R")
        print(f"  By setup:")
        for setup, st in s['by_setup'].items():
            print(f"    {setup:8s}: {st['trades']:3d} trades, {st['win_rate']*100:4.1f}% win, {st['total_r']:+.1f}R")

    return s, bt


def main():
    p = argparse.ArgumentParser(description="Multi-timeframe backtest runner")
    p.add_argument("--tf", required=True, choices=["5m", "15m", "1h"])
    p.add_argument("--csv", required=True)
    p.add_argument("--pm", type=float, default=25.0)
    p.add_argument("--vix"); p.add_argument("--es")
    p.add_argument("--dxy"); p.add_argument("--tick")
    p.add_argument("--compare-all", action="store_true",
                   help="Run 5m, 15m, and 1h backtests and show comparison")
    args = p.parse_args()

    if args.compare_all:
        # Run on all three timeframes and compare
        results = []
        for tf, csv_name in [("5m", args.csv.replace("_15m", "_5m").replace("_1h", "_5m")),
                              ("15m", args.csv.replace("_5m", "_15m").replace("_1h", "_15m")),
                              ("1h", args.csv.replace("_5m", "_1h").replace("_15m", "_1h"))]:
            try:
                s, bt = run_tf_backtest(csv_name, tf, args.pm, verbose=False)
                results.append((tf, csv_name, s))
            except FileNotFoundError:
                print(f"Skipping {tf}: {csv_name} not found")

        # Comparison table
        print(f"\n{'='*90}")
        print(f"MULTI-TF COMPARISON @ PM={args.pm}")
        print(f"{'='*90}")
        print(f"{'TF':>4s}  {'Bars':>6s}  {'Trades':>7s}  {'Wins':>5s}  {'Strike':>7s}  {'TotR':>8s}  {'PF':>5s}  {'MDD':>5s}")
        print("-" * 70)
        for tf, csv_name, s in results:
            if s.get('trades', 0) > 0:
                w = s.get('wins', 0); l = s.get('losses', 0)
                strike = (w / (w+l) * 100) if w+l > 0 else 0
                pf = s['profit_factor'] if isinstance(s['profit_factor'], (int, float)) else 999
                print(f"{tf:>4s}  {s.get('bars', 0):>6d}  {s['trades']:>7d}  {w:>5d}  "
                      f"{strike:>6.1f}%  {s['total_r']:>+8.2f}  {pf:>5.1f}  {s['max_drawdown_r']:>5.2f}")
            else:
                print(f"{tf:>4s}  {s.get('bars', 0):>6d}  {0:>7d}  no trades")
    else:
        run_tf_backtest(args.csv, args.tf, args.pm,
                        vix=args.vix, es=args.es, dxy=args.dxy, tick=args.tick,
                        verbose=True)


if __name__ == "__main__":
    main()
