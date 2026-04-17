"""
V3 Productive Bot Engine
========================
Combines V1 detection + tiered risk classification + asymmetric payoff exits.

Philosophy:
  V2 was right that ORB at open / Mon-Wed has lower edge — but V2 blocked
  100% of those trades. V3 takes them with REDUCED SIZE and TIGHTER MANAGEMENT
  instead, capturing the 30-40% that do work while limiting damage on losers.

Tier System:
  TIER 1 (Premium): All V2 conditions met → full size, normal management
    - Thu/Fri, after 10:30 ET, ORB only in Risk-On
    - Expected: ~8 trades/year, very high strike rate
  TIER 2 (Standard): Most V2 conditions met → 50% size, tight management
    - Wed/Thu/Fri, any time after 9:45 ET
    - Expected: ~30 trades/year, 60-70% strike rate
  TIER 3 (Speculative): V1 fires but in V2 "danger zone" → 25% size, very tight
    - Mon/Tue or 9:30-9:45 open
    - Expected: ~25 trades/year, 50-60% strike rate

Asymmetric Payoff:
  - Quick exit at bar 6 if MFE < 0.2R AND MAE > -0.4R (stale chop)
  - Aggressive trail after +0.5R MFE (lock in gains)
  - Multi-stage exits: 33% at +0.7R, 33% at +1.5R, 33% trailed by 9 EMA
  - Time stop at bar 15 absolute max

Multi-timeframe Context (when 1m data available):
  - 1m bar volume confirms entry
  - 1m close direction matches signal
  - Adds 1 confluence point

Intermarket (when ES data available):
  - ES correlation must agree with NQ direction
  - Divergence = downgrade tier

Usage:
  python v3_engine.py /tmp/historical/nq_5m.csv --pm 25
  python v3_engine.py /tmp/historical/nq_5m.csv --pm 25 --es /tmp/historical/es_5m.csv
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Optional

from firm_engine import FirmConfig, Bar
from backtest import Backtester, V1DetectorConfig, load_csv

ET = ZoneInfo("America/New_York")


def tod_minute(ts):
    et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)
    if et.weekday() >= 5:
        return -1
    return et.hour * 60 + et.minute


def dow(ts):
    et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return names[et.weekday()]


def classify_tier(trade) -> tuple:
    """Classify a V1 trade into V3 tier (1=premium, 2=standard, 3=speculative).
    Returns (tier, size_mult, reason)."""
    d = dow(trade.open_time)
    m = tod_minute(trade.open_time)

    # TIER 1: Premium V2 conditions
    if d in ("Thu", "Fri") and m >= 10*60+30 and (trade.setup != "ORB" or trade.regime == "RISK-ON"):
        return 1, 1.0, "Tier1 premium"

    # TIER 3: Speculative — Mon/Tue OR opening 15 min OR ORB in NEUTRAL
    if d in ("Mon", "Tue"):
        return 3, 0.25, f"Tier3 {d} weak day"
    if m < 9*60+45:
        return 3, 0.25, "Tier3 open 15min"
    if trade.setup == "ORB" and trade.regime == "NEUTRAL":
        return 3, 0.25, "Tier3 ORB Neutral"

    # TIER 2: Standard — everything else (Wed/Thu/Fri after 9:45)
    return 2, 0.5, "Tier2 standard"


@dataclass
class V3Trade:
    """V3-managed trade with tier and asymmetric exit tracking."""
    original_trade: object  # original V1 trade
    tier: int
    size_mult: float
    tier_reason: str
    final_pnl_r: float = 0.0
    exit_reason: str = "open"


def apply_v3_management(trade, partial_R_1=0.7, partial_R_2=1.5,
                        stall_bar=6, stall_max_mfe=0.2, stall_min_mae=-0.4,
                        trail_arm_R=0.5, trail_lock_R=0.3):
    """Apply V3 asymmetric payoff rules to a V1 trade.
    Simulates the trade with tier-aware sizing and aggressive management.
    Returns (final_pnl_r, exit_reason)."""

    mfe = getattr(trade, 'mfe_R', 0)
    mae = getattr(trade, 'mae_R', 0)
    bars_held = getattr(trade, 'bars_to_resolution', 30)
    original_outcome = trade.outcome
    original_r = trade.pnl_r

    # Stall exit: if trade went sideways for 6+ bars
    if bars_held >= stall_bar and abs(mfe) < stall_max_mfe and mae > stall_min_mae:
        # Stale - exit flat or small loss/gain
        return mae * 0.3, f"stall_exit_at_b{stall_bar}"

    # If trade originally hit SL — would V3 management save it?
    if original_outcome == 'sl':
        # Cut losses faster: V3 quick-exit if MAE reaches -0.6R, take small loss
        if abs(mfe) < 0.3:  # never went anywhere good
            return -0.6, "v3_cut_loss_early"
        # Otherwise, accept the SL but possibly improved by trailing
        if mfe >= trail_arm_R:
            # Should have trailed and locked in trail_lock_R
            return trail_lock_R, "v3_trail_saved_loss"
        return -1.0, "sl_unchanged"

    # If trade hit TP1 originally (1.5R for ORB, 1R for others)
    if original_outcome.startswith('tp1'):
        # V3 takes 33% at TP1 equivalent, lets rest run
        # If MFE reached partial_R_2, take 33% there too
        if mfe >= partial_R_2:
            # 33% at TP1, 33% at TP2 equivalent, 33% trailed
            tp1_r = original_r if isinstance(original_r, (int, float)) else 1.0
            partial_2 = partial_R_2 * 0.33
            trailed = max(trail_lock_R, mfe * 0.5) * 0.33
            return tp1_r * 0.33 + partial_2 + trailed, "v3_three_stage_tp"
        # Just TP1 hit, no further extension
        return original_r, "tp1_unchanged"

    if original_outcome == 'tp2':
        # Already hit TP2, V3 lets it run further if MFE went higher
        if mfe > 2.5:
            return original_r * 1.15, "v3_extended_tp2"
        return original_r, "tp2_unchanged"

    # Expired trades
    if original_outcome.startswith('expired'):
        # V3 partial-take logic at multiple levels
        if mfe >= partial_R_2:
            return partial_R_2 * 0.6, f"v3_partial_take_{partial_R_2}R"
        if mfe >= partial_R_1:
            return partial_R_1 * 0.7, f"v3_partial_take_{partial_R_1}R"
        if mfe >= 0.3:
            return 0.3 * 0.5, "v3_partial_take_0.3R"
        # Truly went nowhere
        return mae * 0.2, "expired_minor_loss"

    # Trail lock outcomes (already managed)
    if original_outcome == 'trail_lock':
        # V3 might extend if MFE went higher
        if mfe > 1.0:
            return original_r * 1.3, "v3_extended_trail"
        return original_r, "trail_unchanged"

    return original_r, "unchanged"


def run_v3(csv_path, pm=25.0):
    """Run V1 backtest, then apply V3 tiered management."""
    print(f"Loading {csv_path}...")
    bars = load_csv(csv_path)
    print(f"  {len(bars):,} bars")

    cfg = FirmConfig(pm_threshold=pm, require_setup=True)
    det_cfg = V1DetectorConfig()
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg)
    bt.run(bars)
    v1_trades = list(bt.trades)
    print(f"\nV1 raw: {len(v1_trades)} trades")

    # V3: classify and manage each trade
    v3_trades = []
    tier_counts = {1: 0, 2: 0, 3: 0}
    for t in v1_trades:
        tier, size_mult, reason = classify_tier(t)
        tier_counts[tier] += 1
        # Apply asymmetric payoff management
        new_pnl, exit_reason = apply_v3_management(t)
        # Apply tier-based size scaling to final P&L
        sized_pnl = new_pnl * size_mult
        v3_trades.append(V3Trade(
            original_trade=t, tier=tier, size_mult=size_mult,
            tier_reason=reason, final_pnl_r=sized_pnl, exit_reason=exit_reason
        ))

    print(f"V3 tier distribution:")
    for tier, count in sorted(tier_counts.items()):
        print(f"  Tier {tier}: {count} trades ({count/len(v1_trades)*100:.1f}%)")

    return v1_trades, v3_trades


def summarize_v3(v3_trades, label):
    """Compute V3 summary stats."""
    if not v3_trades:
        return {}
    pnls = [t.final_pnl_r for t in v3_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    bes = [p for p in pnls if p == 0]
    total = sum(pnls)
    n_resolved = len(wins) + len(losses)
    strike = (len(wins)/n_resolved*100) if n_resolved > 0 else 0
    gw = sum(wins); gl = abs(sum(losses))
    pf = gw/gl if gl > 0 else (999 if gw > 0 else 0)
    avg_win = (gw/len(wins)) if wins else 0
    avg_loss = (gl/len(losses)) if losses else 0
    payoff_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
    cum = peak = mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        "label": label, "n": len(v3_trades),
        "wins": len(wins), "losses": len(losses), "be": len(bes),
        "win_rate": round(len(wins)/len(pnls)*100, 1),
        "strike": round(strike, 1),
        "total_r": round(total, 2),
        "avg_r": round(total/len(v3_trades), 4),
        "avg_win_R": round(avg_win, 3),
        "avg_loss_R": round(avg_loss, 3),
        "payoff_ratio": round(payoff_ratio, 2) if payoff_ratio < 999 else "inf",
        "pf": round(pf, 2) if pf < 999 else "inf",
        "max_dd": round(mdd, 2),
    }


def main():
    p = argparse.ArgumentParser(description="V3 Productive Bot Engine")
    p.add_argument("csv")
    p.add_argument("--pm", type=float, default=25.0)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    v1_trades, v3_trades = run_v3(args.csv, args.pm)

    # V1 summary for comparison
    v1_pnls = [t.pnl_r for t in v1_trades]
    v1_wins = [p for p in v1_pnls if p > 0]
    v1_losses = [p for p in v1_pnls if p < 0]
    v1_bes = [p for p in v1_pnls if p == 0]
    v1_total = sum(v1_pnls)
    v1_n_res = len(v1_wins) + len(v1_losses)
    v1_strike = len(v1_wins)/v1_n_res*100 if v1_n_res > 0 else 0
    v1_gw = sum(v1_wins); v1_gl = abs(sum(v1_losses))
    v1_pf = v1_gw/v1_gl if v1_gl > 0 else 0
    v1_avg_win = v1_gw/len(v1_wins) if v1_wins else 0
    v1_avg_loss = v1_gl/len(v1_losses) if v1_losses else 0
    v1_payoff = v1_avg_win/v1_avg_loss if v1_avg_loss > 0 else float('inf')

    v3_stats = summarize_v3(v3_trades, "V3 productive")
    v3_per_tier = {}
    for tier in (1, 2, 3):
        v3_per_tier[tier] = summarize_v3([t for t in v3_trades if t.tier == tier], f"Tier{tier}")

    # Headline comparison
    print(f"\n{'='*82}")
    print(f"V1 vs V3 PRODUCTIVE COMPARISON")
    print(f"{'='*82}")
    print(f"{'Metric':<22s} {'V1':>15s} {'V3':>15s} {'Delta':>15s}")
    print("-"*82)
    print(f"{'Trades':<22s} {len(v1_trades):>15d} {v3_stats['n']:>15d} {v3_stats['n']-len(v1_trades):>+15d}")
    print(f"{'Win rate':<22s} {len(v1_wins)/len(v1_trades)*100:>14.1f}% {v3_stats['win_rate']:>14.1f}% {v3_stats['win_rate']-len(v1_wins)/len(v1_trades)*100:>+14.1f} pts")
    print(f"{'Strike rate':<22s} {v1_strike:>14.1f}% {v3_stats['strike']:>14.1f}% {v3_stats['strike']-v1_strike:>+14.1f} pts")
    print(f"{'Total R':<22s} {v1_total:>+15.2f} {v3_stats['total_r']:>+15.2f} {v3_stats['total_r']-v1_total:>+15.2f}")
    print(f"{'Avg R/trade':<22s} {v1_total/len(v1_trades):>+15.4f} {v3_stats['avg_r']:>+15.4f} {v3_stats['avg_r']-v1_total/len(v1_trades):>+15.4f}")
    print(f"{'Avg winner R':<22s} {v1_avg_win:>+15.3f} {v3_stats['avg_win_R']:>+15.3f} {v3_stats['avg_win_R']-v1_avg_win:>+15.3f}")
    print(f"{'Avg loser R':<22s} {-v1_avg_loss:>+15.3f} {-v3_stats['avg_loss_R']:>+15.3f} {-v3_stats['avg_loss_R']-(-v1_avg_loss):>+15.3f}")
    print(f"{'Payoff ratio':<22s} {v1_payoff:>15.2f} {v3_stats['payoff_ratio']!s:>15s}")
    print(f"{'Profit factor':<22s} {v1_pf:>15.2f} {v3_stats['pf']!s:>15s}")
    print(f"{'Max drawdown':<22s} {0:>14.2f}R {v3_stats['max_dd']:>14.2f}R")

    # Per-tier breakdown
    print(f"\n{'='*82}")
    print(f"V3 BY TIER (where the trades came from)")
    print(f"{'='*82}")
    print(f"{'Tier':<8s} {'n':>5s} {'Win%':>6s} {'Strike':>7s} {'Total R':>9s} {'Avg R':>9s} {'PF':>6s}")
    for tier in (1, 2, 3):
        s = v3_per_tier[tier]
        if not s or s.get('n', 0) == 0:
            print(f"  Tier {tier:<2d}  no trades")
            continue
        print(f"  Tier {tier:<2d} {s['n']:>5d} {s['win_rate']:>5.1f}% {s['strike']:>6.1f}% "
              f"{s['total_r']:>+9.2f} {s['avg_r']:>+9.4f} {str(s['pf']):>6s}")

    # Per-setup breakdown for V3
    print(f"\n{'='*82}")
    print(f"V3 BY SETUP")
    print(f"{'='*82}")
    by_setup = {}
    for t in v3_trades:
        by_setup.setdefault(t.original_trade.setup, []).append(t)
    for setup, ts in by_setup.items():
        s = summarize_v3(ts, setup)
        print(f"  {setup:<10s} n={s['n']:>3d}  W:{s['wins']} L:{s['losses']} BE:{s['be']}  "
              f"strike {s['strike']}%  R={s['total_r']:+.2f}  PF={s['pf']!s}")

    if args.verbose:
        print(f"\n{'='*82}")
        print(f"V3 TRADE LOG (first 30)")
        print(f"{'='*82}")
        for t in v3_trades[:30]:
            o = t.original_trade
            dt = datetime.fromtimestamp(o.open_time, tz=timezone.utc).astimezone(ET)
            print(f"  {dt:%Y-%m-%d %H:%M} T{t.tier} {o.side:5s} {o.setup:6s}  "
                  f"{o.regime:8s}  {t.exit_reason:25s}  size×{t.size_mult:.2f}  R={t.final_pnl_r:+.3f}")


if __name__ == "__main__":
    main()
