"""
Master Test Suite
=================
A/B comparison of feature combinations to demonstrate which optimizations
actually move the needle on real data.

Tests:
  1. Baseline (no intermarket, no circuit breaker)
  2. + Intermarket voices only
  3. + Circuit breaker only
  4. + Both (full v2 fine-tuned)

For each, runs at the auto-calibrated PM and reports same metrics.
"""

import argparse
from firm_engine import FirmConfig
from backtest import Backtester, V1DetectorConfig, load_csv
from intermarket import load_with_intermarket, coverage_report


def run_config(label, bars, pm, use_cb=True, red_w=1.3, orb_to=20, ema_s=4):
    cfg = FirmConfig(pm_threshold=pm, redteam_weight=red_w, require_setup=True)
    if not use_cb:
        cfg.daily_loss_pause = -100.0
        cfg.daily_loss_half_size = -100.0
    det_cfg = V1DetectorConfig(orb_timeout=orb_to, ema_min_score=ema_s)
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg)
    s = bt.run(bars)
    return label, s, bt


def fmt(s, bt):
    if s.get("trades", 0) == 0:
        return f"  {0:>3d} trades                                         (CB blocks: {bt.blocked_by_circuit_breaker})"
    wins = s.get("wins", 0); losses = s.get("losses", 0); bes = s.get("breakevens", 0)
    resolved = wins + losses
    strike = (wins / resolved * 100) if resolved > 0 else 0
    pf = s["profit_factor"] if isinstance(s["profit_factor"], (int, float)) else 999
    return (f"  {s['trades']:>3d} trades  W:{wins} L:{losses} BE:{bes}  "
            f"win {s['win_rate']:>4.1f}%  strike {strike:>4.1f}%  "
            f"R={s['total_r']:>+6.2f}  PF={pf:>4.1f}  MDD={s['max_drawdown_r']:>4.2f}R  "
            f"(CB blocks: {bt.blocked_by_circuit_breaker})")


def main():
    p = argparse.ArgumentParser(description="Apex v2 Master Test Suite")
    p.add_argument("csv")
    p.add_argument("--vix"); p.add_argument("--es")
    p.add_argument("--dxy"); p.add_argument("--tick")
    p.add_argument("--pm", type=float, default=30.0,
                   help="PM threshold to use across all tests (default 30, calibrated)")
    args = p.parse_args()

    print(f"{'='*80}")
    print(f"APEX v2 MASTER TEST SUITE")
    print(f"{'='*80}\n")

    # Load data without intermarket
    bars_plain = load_csv(args.csv)
    # Load with intermarket
    bars_im = load_with_intermarket(args.csv, vix=args.vix, es=args.es,
                                     dxy=args.dxy, tick=args.tick)
    cov = coverage_report(bars_im)
    print(f"Bars: {len(bars_plain)}")
    print(f"Intermarket coverage: VIX {cov['with_vix']/cov['total_bars']*100:.0f}%, "
          f"ES {cov['with_es']/cov['total_bars']*100:.0f}%, "
          f"DXY {cov['with_dxy']/cov['total_bars']*100:.0f}%, "
          f"TICK {cov['with_tick']/cov['total_bars']*100:.0f}%")
    print(f"PM threshold: {args.pm}")
    print()

    # Run all 4 configurations
    print(f"{'─'*80}")
    print(f"TEST 1: Baseline (no intermarket, no circuit breaker)")
    print(f"{'─'*80}")
    label, s, bt = run_config("baseline", bars_plain, args.pm, use_cb=False)
    print(fmt(s, bt))

    print(f"\n{'─'*80}")
    print(f"TEST 2: + Intermarket voices (V8-V11)")
    print(f"{'─'*80}")
    label, s, bt = run_config("intermarket", bars_im, args.pm, use_cb=False)
    print(fmt(s, bt))

    print(f"\n{'─'*80}")
    print(f"TEST 3: + Daily P&L circuit breaker only")
    print(f"{'─'*80}")
    label, s, bt = run_config("circuitbreaker", bars_plain, args.pm, use_cb=True)
    print(fmt(s, bt))

    print(f"\n{'─'*80}")
    print(f"TEST 4: + Both (full v2 fine-tuned)")
    print(f"{'─'*80}")
    label, s, bt = run_config("full", bars_im, args.pm, use_cb=True)
    print(fmt(s, bt))

    # Per-setup breakdown of full config
    print(f"\n{'─'*80}")
    print(f"FULL v2 BY SETUP")
    print(f"{'─'*80}")
    for setup, st in s.get("by_setup", {}).items():
        print(f"  {setup:8s}: {st['trades']:3d} trades, {st['win_rate']*100:5.1f}% win, total {st['total_r']:+.1f}R")

    print(f"\n{'─'*80}")
    print(f"FULL v2 BY REGIME")
    print(f"{'─'*80}")
    for reg, st in s.get("by_regime", {}).items():
        print(f"  {reg:10s}: {st['trades']:3d} trades, {st['win_rate']*100:5.1f}% win, total {st['total_r']:+.1f}R")

    # Equity curve summary
    if bt.equity_curve:
        print(f"\n{'─'*80}")
        print(f"EQUITY CURVE (full v2)")
        print(f"{'─'*80}")
        print(f"  Start:  R=0.00")
        peak = 0; trough_r = 0
        for t, r in bt.equity_curve:
            peak = max(peak, r)
            trough_r = min(trough_r, r - peak)  # running drawdown
        print(f"  Peak:   R={peak:+.2f}")
        print(f"  Final:  R={bt.equity_curve[-1][1]:+.2f}")
        print(f"  Worst running DD: {trough_r:.2f}R")
        # Print a few sample points
        n = len(bt.equity_curve)
        if n >= 5:
            samples = [bt.equity_curve[i] for i in [0, n//4, n//2, 3*n//4, n-1]]
            from datetime import datetime, timezone
            print(f"\n  Curve samples:")
            for t, r in samples:
                dt = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                print(f"    {dt}  R={r:+.2f}")


if __name__ == "__main__":
    main()
