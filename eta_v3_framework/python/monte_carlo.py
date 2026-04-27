"""
Apex v2 Monte Carlo & Stress Test Suite
========================================
Bootstrap resampling on backtest trades to estimate confidence intervals
on key metrics. Stress tests simulate worst-case streaks and recovery time.

Key outputs:
- 95% CI on total R, max DD, win rate, profit factor
- Probability of ruin (-3R drawdown)
- Worst-case longest losing streak
- Average recovery time from drawdown

Usage:
    python monte_carlo.py mnq_5m.csv --pm 30 --sims 1000
    python monte_carlo.py mnq_5m.csv --pm 30 --sims 1000 --vix vix.csv --es es.csv
"""

import argparse
import random
import statistics

from backtest import Backtester, V1DetectorConfig, load_csv
from firm_engine import FirmConfig


def run_backtest(csv_path, pm, vix=None, es=None, dxy=None, tick=None, use_kelly=False, slip_R=0.0):
    """Run the backtest and return the trade R sequence."""
    if vix or es or dxy or tick:
        from intermarket import load_with_intermarket

        bars = load_with_intermarket(csv_path, vix=vix, es=es, dxy=dxy, tick=tick)
    else:
        bars = load_csv(csv_path)
    cfg = FirmConfig(pm_threshold=pm, require_setup=True)
    det_cfg = V1DetectorConfig()
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg, use_kelly=use_kelly, slip_per_trade_R=slip_R)
    bt.run(bars)
    return [t.pnl_r for t in bt.trades]


def equity_stats(trade_seq):
    """Compute equity curve statistics from a trade sequence."""
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    wins = 0
    losses = 0
    streak_loss_max = 0
    streak_loss_cur = 0
    streak_win_max = 0
    streak_win_cur = 0
    for r in trade_seq:
        cum += r
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)
        if r > 0:
            wins += 1
            streak_win_cur += 1
            streak_loss_cur = 0
            streak_win_max = max(streak_win_max, streak_win_cur)
        elif r < 0:
            losses += 1
            streak_loss_cur += 1
            streak_win_cur = 0
            streak_loss_max = max(streak_loss_max, streak_loss_cur)
    n_resolved = wins + losses
    win_rate = (wins / n_resolved * 100) if n_resolved > 0 else 0
    gross_w = sum(r for r in trade_seq if r > 0)
    gross_l = abs(sum(r for r in trade_seq if r < 0))
    pf = gross_w / gross_l if gross_l > 0 else (999.0 if gross_w > 0 else 0)
    return {
        "total_r": cum,
        "max_dd": max_dd,
        "win_rate": win_rate,
        "pf": pf,
        "longest_loss_streak": streak_loss_max,
        "longest_win_streak": streak_win_max,
        "n_trades": len(trade_seq),
    }


def monte_carlo(trade_seq, n_sims=1000, ruin_dd=3.0):
    """Bootstrap resample trade sequence n_sims times, track stat distributions."""
    if len(trade_seq) < 5:
        return None
    all_stats = []
    ruin_count = 0
    for _ in range(n_sims):
        # Sample WITH replacement, same length as original
        sample = random.choices(trade_seq, k=len(trade_seq))
        s = equity_stats(sample)
        all_stats.append(s)
        if s["max_dd"] >= ruin_dd:
            ruin_count += 1

    def ci(key, pct):
        vals = sorted([s[key] for s in all_stats])
        idx = int(len(vals) * pct / 100)
        return vals[min(idx, len(vals) - 1)]

    return {
        "n_sims": n_sims,
        "total_r_p5": round(ci("total_r", 5), 2),
        "total_r_p50": round(ci("total_r", 50), 2),
        "total_r_p95": round(ci("total_r", 95), 2),
        "max_dd_p5": round(ci("max_dd", 5), 2),
        "max_dd_p50": round(ci("max_dd", 50), 2),
        "max_dd_p95": round(ci("max_dd", 95), 2),
        "win_rate_p5": round(ci("win_rate", 5), 1),
        "win_rate_p50": round(ci("win_rate", 50), 1),
        "win_rate_p95": round(ci("win_rate", 95), 1),
        "pf_p50": round(ci("pf", 50), 2),
        "longest_loss_streak_p95": ci("longest_loss_streak", 95),
        "ruin_probability_pct": round(ruin_count / n_sims * 100, 2),
    }


def stress_test(trade_seq):
    """Worst-case scenario analysis."""
    if not trade_seq:
        return None
    # Sort losses descending (worst first), simulate worst-case streak
    losses_sorted = sorted([r for r in trade_seq if r < 0])
    worst_5_losses = losses_sorted[:5]
    worst_streak_dd = sum(worst_5_losses)

    # Original equity stats
    equity_stats(trade_seq)

    # Simulate adding 3 consecutive worst losses to current curve
    pessimistic = trade_seq + ([losses_sorted[0]] * 3 if losses_sorted else [])
    pess_stats = equity_stats(pessimistic)

    # Recovery time: how many trades to recover from max DD
    cum = 0.0
    peak = 0.0
    in_dd = False
    dd_start = 0
    recovery_times = []
    for i, r in enumerate(trade_seq):
        cum += r
        if cum > peak:
            if in_dd:
                recovery_times.append(i - dd_start)
                in_dd = False
            peak = cum
        elif peak - cum > 0.5 and not in_dd:
            in_dd = True
            dd_start = i
    avg_recovery = statistics.mean(recovery_times) if recovery_times else 0

    return {
        "worst_5_losses_total": round(worst_streak_dd, 2),
        "worst_single_loss": round(losses_sorted[0], 2) if losses_sorted else 0,
        "pessimistic_total_r": round(pess_stats["total_r"], 2),
        "pessimistic_max_dd": round(pess_stats["max_dd"], 2),
        "avg_recovery_trades": round(avg_recovery, 1),
        "n_drawdown_periods": len(recovery_times),
    }


def main():
    p = argparse.ArgumentParser(description="Apex v2 Monte Carlo & Stress Test Suite")
    p.add_argument("csv")
    p.add_argument("--pm", type=float, default=30.0)
    p.add_argument("--sims", type=int, default=1000)
    p.add_argument(
        "--ruin-dd",
        type=float,
        default=3.0,
        help="Drawdown level (R) considered 'ruin' for probability calc",
    )
    p.add_argument("--vix")
    p.add_argument("--es")
    p.add_argument("--dxy")
    p.add_argument("--tick")
    p.add_argument("--kelly", action="store_true")
    p.add_argument(
        "--slip", type=float, default=0.0, help="Slippage R per trade (e.g. 0.05 for MNQ realistic)"
    )
    args = p.parse_args()

    print(f"{'=' * 70}")
    print("APEX v2 MONTE CARLO & STRESS TEST")
    print(f"{'=' * 70}")
    print(f"Data: {args.csv}")
    print(
        f"PM threshold: {args.pm}  Sims: {args.sims}  Slippage: {args.slip}R/trade  Kelly: {args.kelly}\n"
    )

    print("Running backtest to extract trade sequence...")
    trade_seq = run_backtest(
        args.csv,
        args.pm,
        vix=args.vix,
        es=args.es,
        dxy=args.dxy,
        tick=args.tick,
        use_kelly=args.kelly,
        slip_R=args.slip,
    )
    print(f"Got {len(trade_seq)} trades from backtest.")
    if not trade_seq:
        print("No trades — cannot run Monte Carlo.")
        return

    print(f"\n{'─' * 70}")
    print("ORIGINAL BACKTEST STATS")
    print(f"{'─' * 70}")
    orig = equity_stats(trade_seq)
    print(f"  Trades:          {orig['n_trades']}")
    print(f"  Total R:         {orig['total_r']:+.2f}")
    print(f"  Max DD:          {orig['max_dd']:.2f}R")
    print(f"  Win rate:        {orig['win_rate']:.1f}%")
    print(f"  Profit factor:   {orig['pf']:.2f}")
    print(f"  Longest loss:    {orig['longest_loss_streak']} trades")
    print(f"  Longest win:     {orig['longest_win_streak']} trades")

    print(f"\n{'─' * 70}")
    print(f"MONTE CARLO ({args.sims} simulations, bootstrap resampling)")
    print(f"{'─' * 70}")
    mc = monte_carlo(trade_seq, n_sims=args.sims, ruin_dd=args.ruin_dd)
    if mc:
        print(f"\n                    {'5th %ile':>10s}  {'Median':>10s}  {'95th %ile':>10s}")
        print(
            f"  Total R:          {mc['total_r_p5']:>+10.2f}  {mc['total_r_p50']:>+10.2f}  {mc['total_r_p95']:>+10.2f}"
        )
        print(
            f"  Max drawdown:     {mc['max_dd_p5']:>10.2f}  {mc['max_dd_p50']:>10.2f}  {mc['max_dd_p95']:>10.2f}"
        )
        print(
            f"  Win rate:         {mc['win_rate_p5']:>9.1f}%  {mc['win_rate_p50']:>9.1f}%  {mc['win_rate_p95']:>9.1f}%"
        )
        print(f"\n  Median PF:                          {mc['pf_p50']:.2f}")
        print(f"  Longest loss streak (95th %ile):    {mc['longest_loss_streak_p95']}")
        print(f"  P(ruin >= {args.ruin_dd}R drawdown):           {mc['ruin_probability_pct']:.2f}%")

    print(f"\n{'─' * 70}")
    print("STRESS TEST")
    print(f"{'─' * 70}")
    stress = stress_test(trade_seq)
    if stress:
        print(f"  Worst single loss:           {stress['worst_single_loss']:+.2f}R")
        print(f"  Worst 5 losses (total):      {stress['worst_5_losses_total']:+.2f}R")
        print(f"  Pessimistic total R:         {stress['pessimistic_total_r']:+.2f}R")
        print(f"  Pessimistic max DD:          {stress['pessimistic_max_dd']:.2f}R")
        print(f"  Avg recovery time:           {stress['avg_recovery_trades']} trades")
        print(f"  # drawdown periods:          {stress['n_drawdown_periods']}")

    # Verdict
    print(f"\n{'=' * 70}")
    print("VERDICT")
    print(f"{'=' * 70}")
    pass_ci = mc["total_r_p5"] > 0 if mc else False
    pass_dd = mc["max_dd_p95"] <= 2.5 if mc else False
    pass_ruin = mc["ruin_probability_pct"] < 5.0 if mc else False
    print(f"  ✓ 5th %ile total R > 0:       {pass_ci}  ({mc['total_r_p5']:+.2f}R)" if mc else "—")
    print(f"  ✓ 95th %ile DD <= 2.5R:       {pass_dd}  ({mc['max_dd_p95']:.2f}R)" if mc else "—")
    print(
        f"  ✓ Ruin probability < 5%:      {pass_ruin}  ({mc['ruin_probability_pct']:.2f}%)"
        if mc
        else "—"
    )
    print()
    if pass_ci and pass_dd and pass_ruin:
        print("  >> SYSTEM PASSES MONTE CARLO VALIDATION <<")
    elif pass_ci or pass_dd:
        print("  >> SYSTEM MARGINAL — review high-DD scenarios before scaling capital <<")
    else:
        print("  >> SYSTEM FAILS — current sample suggests no edge or excessive variance <<")


if __name__ == "__main__":
    main()
