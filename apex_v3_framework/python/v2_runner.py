"""
V2 Runner — Edge Spec Applied
==============================
Wraps locked V1 backtest with the data-derived V2 filters.
DOES NOT modify V1 code. Filters happen at the trade-acceptance layer.

Rules applied (from EDGE_SPEC_V2.md, OOS-validated):
  R1: DOW filter (Thu/Fri only)
  R2: TOD filter (skip 9:30-10:30 ET)
  R3: Regime filter (ORB only in RISK-ON)
  E1: 0.5R partial-take on stalled trades

Usage:
  python v2_runner.py /tmp/historical/nq_5m.csv --pm 25
"""

import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from firm_engine import FirmConfig, Bar
from backtest import Backtester, V1DetectorConfig, load_csv

ET = ZoneInfo("America/New_York")


def tod_bucket(ts):
    et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)
    m = et.hour * 60 + et.minute
    if et.weekday() >= 5: return "weekend"
    if m < 9*60+30: return "premarket"
    if m < 10*60+30: return "open_30min"
    if m < 11*60+30: return "mid_am"
    if m < 13*60+30: return "lunch"
    if m < 14*60+30: return "early_pm"
    if m < 15*60+30: return "power_hour"
    if m < 16*60: return "moc"
    return "after_hours"


def is_v2_eligible(trade) -> tuple:
    """Apply V2 filters to a V1 trade. Returns (eligible, reason_if_not)."""
    et = datetime.fromtimestamp(trade.open_time, tz=timezone.utc).astimezone(ET)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow = dow_names[et.weekday()]

    # R1: DOW filter
    if dow not in ("Thu", "Fri"):
        return False, f"R1: {dow} not allowed"

    # R2: TOD filter
    tod = tod_bucket(trade.open_time)
    if tod in ("open_30min", "premarket", "after_hours", "weekend"):
        return False, f"R2: {tod} not allowed"

    # R3: ORB only in RISK-ON
    if trade.setup == "ORB" and trade.regime != "RISK-ON":
        return False, f"R3: ORB blocked in {trade.regime}"

    return True, ""


def apply_partial_take(trades, partial_R=0.5):
    """E1: Replace 'expired' outcomes with 0.5R if MFE >= 0.5R reached."""
    for t in trades:
        if t.outcome.startswith('expired'):
            mfe = getattr(t, 'mfe_R', 0)
            if mfe >= partial_R:
                t.pnl_r = partial_R
                t.outcome = f'partial_take_{partial_R}R'


def run_v2(csv_path, pm=25.0, verbose=False):
    """Run V1 backtest then apply V2 filters."""
    print(f"Loading {csv_path}...")
    bars = load_csv(csv_path)
    print(f"  {len(bars):,} bars")

    cfg = FirmConfig(pm_threshold=pm, require_setup=True)
    det_cfg = V1DetectorConfig()
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg)
    bt.run(bars)
    v1_trades = list(bt.trades)
    print(f"\nV1 raw output: {len(v1_trades)} trades")

    # Apply V2 filters
    v2_trades = []
    rejected = {"R1": 0, "R2": 0, "R3": 0}
    for t in v1_trades:
        eligible, reason = is_v2_eligible(t)
        if eligible:
            v2_trades.append(t)
        else:
            rule = reason.split(":")[0]
            rejected[rule] = rejected.get(rule, 0) + 1

    print(f"V2 after filters: {len(v2_trades)} trades")
    print(f"  Rejected by R1 (DOW): {rejected.get('R1', 0)}")
    print(f"  Rejected by R2 (TOD): {rejected.get('R2', 0)}")
    print(f"  Rejected by R3 (Regime): {rejected.get('R3', 0)}")

    # Apply E1: partial-take on stalled trades
    apply_partial_take(v2_trades, partial_R=0.5)

    return v1_trades, v2_trades


def summarize(trades, label):
    if not trades:
        return {"label": label, "n": 0}
    pnls = [t.pnl_r for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    bes = [p for p in pnls if p == 0]
    total = sum(pnls)
    n_resolved = len(wins) + len(losses)
    strike = (len(wins)/n_resolved*100) if n_resolved > 0 else 0
    gw = sum(wins); gl = abs(sum(losses))
    pf = gw/gl if gl > 0 else (999 if gw > 0 else 0)
    cum = peak = mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        "label": label, "n": len(trades),
        "wins": len(wins), "losses": len(losses), "be": len(bes),
        "win_rate": round(len(wins)/len(pnls)*100, 1),
        "strike": round(strike, 1),
        "total_r": round(total, 2),
        "avg_r": round(total/len(trades), 4),
        "pf": pf, "max_dd": round(mdd, 2),
    }


def main():
    p = argparse.ArgumentParser(description="V2 Runner")
    p.add_argument("csv")
    p.add_argument("--pm", type=float, default=25.0)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    v1_trades, v2_trades = run_v2(args.csv, args.pm, args.verbose)
    v1_stats = summarize(v1_trades, "V1 baseline")
    v2_stats = summarize(v2_trades, "V2 filtered")

    print(f"\n{'='*72}")
    print(f"V1 vs V2 COMPARISON")
    print(f"{'='*72}")
    print(f"{'Metric':<20s} {'V1':>15s} {'V2':>15s} {'Delta':>12s}")
    print("-"*72)
    print(f"{'Trades':<20s} {v1_stats['n']:>15d} {v2_stats['n']:>15d} {v2_stats['n']-v1_stats['n']:>+12d}")
    print(f"{'Win rate':<20s} {v1_stats['win_rate']:>14.1f}% {v2_stats['win_rate']:>14.1f}% {v2_stats['win_rate']-v1_stats['win_rate']:>+11.1f} pts")
    print(f"{'Strike rate':<20s} {v1_stats['strike']:>14.1f}% {v2_stats['strike']:>14.1f}% {v2_stats['strike']-v1_stats['strike']:>+11.1f} pts")
    print(f"{'Total R':<20s} {v1_stats['total_r']:>+15.2f} {v2_stats['total_r']:>+15.2f} {v2_stats['total_r']-v1_stats['total_r']:>+12.2f}")
    print(f"{'Avg R/trade':<20s} {v1_stats['avg_r']:>+15.4f} {v2_stats['avg_r']:>+15.4f} {v2_stats['avg_r']-v1_stats['avg_r']:>+12.4f}")
    pf1 = v1_stats['pf'] if v1_stats['pf'] < 999 else 'inf'
    pf2 = v2_stats['pf'] if v2_stats['pf'] < 999 else 'inf'
    print(f"{'Profit factor':<20s} {pf1!s:>15s} {pf2!s:>15s}")
    print(f"{'Max drawdown':<20s} {v1_stats['max_dd']:>14.2f}R {v2_stats['max_dd']:>14.2f}R {v2_stats['max_dd']-v1_stats['max_dd']:>+11.2f}R")

    # By setup
    print(f"\n{'='*72}")
    print(f"V2 BY SETUP")
    print(f"{'='*72}")
    by_setup = {}
    for t in v2_trades:
        by_setup.setdefault(t.setup, []).append(t)
    for setup, ts in by_setup.items():
        s = summarize(ts, setup)
        print(f"  {setup:<10s} n={s['n']:>3d}  W:{s['wins']} L:{s['losses']} BE:{s['be']}  "
              f"strike {s['strike']}%  R={s['total_r']:+.2f}  PF={s['pf'] if s['pf']<999 else 'inf'}")

    if args.verbose:
        print(f"\n{'='*72}")
        print(f"V2 TRADE LOG")
        print(f"{'='*72}")
        for t in v2_trades:
            dt = datetime.fromtimestamp(t.open_time, tz=timezone.utc).astimezone(ET)
            print(f"  {dt:%Y-%m-%d %H:%M} {t.side:5s} {t.setup:6s}  {t.regime:10s}  → {t.outcome:25s} {t.pnl_r:+.2f}R")


if __name__ == "__main__":
    main()
