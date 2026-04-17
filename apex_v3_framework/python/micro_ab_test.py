"""
Microstructure A/B Test
========================
Compares the same backtest run with:
  A) Market entry at 5m close (baseline)
  B) 1m micro-refined entry (ORB confirmation / EMA rejection / Sweep retest)

Measures:
  - Win rate delta
  - Total R delta
  - Average R improvement per trade (tighter stops)
  - Signals skipped by micro mode (no confirmation = trade rejected)

Usage:
  python micro_ab_test.py --csv5 mnq_5m.csv --csv1 mnq_1m.csv --pm 25
"""

import argparse
import statistics
from datetime import datetime, timezone

from firm_engine import FirmConfig
from backtest import Backtester, V1DetectorConfig, load_csv
from microstructure import load_1m_bars
from intermarket import load_with_intermarket


def run_backtest(bars, bars_1m, pm, use_micro, vix=None):
    """Run backtest with or without micro entry mode."""
    cfg = FirmConfig(pm_threshold=pm, require_setup=True)
    det_cfg = V1DetectorConfig()
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg,
                    use_micro_entry=use_micro,
                    bars_1m=bars_1m if use_micro else [])
    s = bt.run(bars)
    return s, bt


def fmt_stats(label, s, bt):
    if s.get('trades', 0) == 0:
        return f"  [{label}] 0 trades (micro_skip: {bt.micro_skip_count})"
    w = s.get('wins', 0); l = s.get('losses', 0); be = s.get('breakevens', 0)
    resolved = w + l
    strike = (w / resolved * 100) if resolved > 0 else 0
    pf = s['profit_factor'] if isinstance(s['profit_factor'], (int, float)) else 999
    return (f"  [{label}] {s['trades']:>2d} trades  W:{w} L:{l} BE:{be}  "
            f"strike {strike:>4.1f}%  R {s['total_r']:>+5.2f}  PF {pf:>4.1f}  "
            f"MDD {s['max_drawdown_r']:>4.2f}R  (micro_skip: {bt.micro_skip_count})")


def main():
    p = argparse.ArgumentParser(description="Microstructure A/B Test")
    p.add_argument("--csv5", required=True, help="5m OHLCV CSV (main signal)")
    p.add_argument("--csv1", required=True, help="1m OHLCV CSV (for micro entry)")
    p.add_argument("--pm", type=float, default=25.0)
    p.add_argument("--vix"); p.add_argument("--es")
    p.add_argument("--dxy"); p.add_argument("--tick")
    args = p.parse_args()

    print(f"{'='*72}")
    print(f"MICROSTRUCTURE A/B TEST  PM={args.pm}")
    print(f"{'='*72}")

    # Load 5m with intermarket
    if args.vix or args.es or args.dxy or args.tick:
        bars_5m = load_with_intermarket(args.csv5, vix=args.vix, es=args.es,
                                         dxy=args.dxy, tick=args.tick)
    else:
        bars_5m = load_csv(args.csv5)
    bars_1m = load_1m_bars(args.csv1)

    print(f"  5m bars: {len(bars_5m)} ({datetime.fromtimestamp(bars_5m[0].time, tz=timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(bars_5m[-1].time, tz=timezone.utc):%Y-%m-%d})")
    print(f"  1m bars: {len(bars_1m)} ({datetime.fromtimestamp(bars_1m[0].time, tz=timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(bars_1m[-1].time, tz=timezone.utc):%Y-%m-%d})")
    # 1m coverage within 5m window
    t1_start = bars_1m[0].time if bars_1m else 0
    bars_5m_in_1m_window = [b for b in bars_5m if b.time >= t1_start]
    print(f"  5m bars within 1m window: {len(bars_5m_in_1m_window)}")
    print()

    # ─── Test A: Market entry (baseline) ───
    print(f"─── A: Market entry (no micro) ───")
    s_a, bt_a = run_backtest(bars_5m, [], args.pm, use_micro=False)
    print(fmt_stats("market", s_a, bt_a))

    # ─── Test B: Micro entry ───
    print(f"\n─── B: 1m micro-refined entry ───")
    s_b, bt_b = run_backtest(bars_5m, bars_1m, args.pm, use_micro=True)
    print(fmt_stats("micro ", s_b, bt_b))

    # Only compare trades that fall within 1m data coverage for fairness
    trades_a_in_window = [t for t in bt_a.trades if t.open_time >= t1_start]
    trades_b_in_window = [t for t in bt_b.trades if t.open_time >= t1_start]

    if trades_a_in_window or trades_b_in_window:
        print(f"\n─── Within 1m data window only ───")
        def window_stats(trades):
            if not trades:
                return 0, 0, 0, 0.0, 0
            w = sum(1 for t in trades if t.pnl_r > 0)
            l = sum(1 for t in trades if t.pnl_r < 0)
            r = sum(t.pnl_r for t in trades)
            return len(trades), w, l, r, (w/(w+l)*100 if w+l>0 else 0)
        na, wa, la, ra, sa = window_stats(trades_a_in_window)
        nb, wb, lb, rb, sb = window_stats(trades_b_in_window)
        print(f"  [market] {na} trades  W:{wa} L:{la}  strike {sa:.1f}%  R {ra:+.2f}")
        print(f"  [micro ] {nb} trades  W:{wb} L:{lb}  strike {sb:.1f}%  R {rb:+.2f}")

    # Micro R improvements
    if bt_b.micro_r_improvements:
        avg_improvement = statistics.mean(bt_b.micro_r_improvements)
        print(f"\n─── Micro R:R improvements ───")
        print(f"  Trades micro-refined:    {len(bt_b.micro_r_improvements)}")
        print(f"  Avg R multiplier:        {avg_improvement:.2f}× (tighter stop)")
        print(f"  Min / max:               {min(bt_b.micro_r_improvements):.2f}× / {max(bt_b.micro_r_improvements):.2f}×")
        print(f"  Signals skipped by micro: {bt_b.micro_skip_count}")
        print(f"  Signal acceptance rate:  {len(bt_b.micro_r_improvements)/(len(bt_b.micro_r_improvements)+bt_b.micro_skip_count)*100:.0f}%" if (len(bt_b.micro_r_improvements)+bt_b.micro_skip_count) > 0 else "")

    # Per-setup breakdown for micro mode
    if bt_b.trades:
        print(f"\n─── B (micro): by setup ───")
        by_setup = {}
        for t in bt_b.trades:
            by_setup.setdefault(t.setup, []).append(t)
        for setup, ts in by_setup.items():
            w = sum(1 for t in ts if t.pnl_r > 0)
            l = sum(1 for t in ts if t.pnl_r < 0)
            r = sum(t.pnl_r for t in ts)
            strike = w/(w+l)*100 if w+l>0 else 0
            print(f"  {setup:8s}: {len(ts):>2d} trades  strike {strike:>4.1f}%  R {r:+.2f}")


if __name__ == "__main__":
    main()
