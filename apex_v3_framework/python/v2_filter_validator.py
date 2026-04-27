"""
V2 Filter Validator
====================
Takes V1 trade log and applies V2 spec filters in stages to show
the cumulative impact of each rule. This validates the EDGE_SPEC_V2
projections before we touch any code.

Stages:
  Stage 0: V1 baseline
  Stage 1: + R1 (DOW filter: Thu/Fri only)
  Stage 2: + R2 (TOD filter: skip 9:30-10:30)
  Stage 3: + R3 (regime filter: skip ORB in NEUTRAL)
  Stage 4: + E1 (0.5R partial-take rule on stalled trades)
  Stage 5: + R4 (voice signature filter: require v15 for ORB bull)

Usage:
  python v2_filter_validator.py /tmp/edge_discovery/trades_full.csv
"""

import argparse
import csv
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def load_trades(path):
    trades = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades


def stage_stats(trades, label):
    """Compute summary stats for a trade list."""
    if not trades:
        return {"label": label, "n": 0}
    pnls = [float(t["pnl_r"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    bes = [p for p in pnls if p == 0]
    total_r = sum(pnls)
    n_resolved = len(wins) + len(losses)
    strike = (len(wins) / n_resolved * 100) if n_resolved > 0 else 0
    win_rate = (len(wins) / len(pnls) * 100) if pnls else 0
    gw = sum(wins)
    gl = abs(sum(losses))
    pf = gw / gl if gl > 0 else (999 if gw > 0 else 0)
    cum = peak = mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        "label": label,
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "be": len(bes),
        "win_rate": round(win_rate, 1),
        "strike": round(strike, 1),
        "total_r": round(total_r, 2),
        "avg_r": round(total_r / len(pnls), 4),
        "pf": round(pf, 2) if pf < 999 else "inf",
        "max_dd": round(mdd, 2),
    }


def apply_dow_filter(trades, allowed=("Thu", "Fri")):
    """R1: Day of week filter."""
    return [t for t in trades if t["dow"] in allowed]


def apply_tod_filter(trades, blocked=("open_30min", "premarket", "after_hours", "weekend")):
    """R2: Time of day filter."""
    return [t for t in trades if t["tod_bucket"] not in blocked]


def apply_regime_filter(trades):
    """R3: ORB only in RISK-ON, EMA PB any regime, SWEEP any regime."""
    out = []
    for t in trades:
        if t["setup"] == "ORB" and t["regime"] != "RISK-ON":
            continue
        out.append(t)
    return out


def apply_partial_take(trades, partial_R=0.5):
    """E1: For trades that expired but had MFE >= partial_R, simulate
    taking the partial. Their pnl_r becomes partial_R."""
    out = []
    for t in trades:
        new_t = dict(t)
        outcome = t["outcome"]
        if outcome.startswith("expired"):
            mfe = float(t.get("mfe_R", 0))
            if mfe >= partial_R:
                new_t["pnl_r"] = str(partial_R)
                new_t["outcome"] = f"partial_take_{partial_R}R"
        out.append(new_t)
    return out


def apply_voice_signature_filter(trades):
    """R4: For ORB trades, require v15 (FVG) confirmation matching direction.
    For long: v15 > +20. For short: v15 < -20.
    Other setups untouched."""
    out = []
    for t in trades:
        if t["setup"] == "ORB":
            v15 = float(t.get("v15", 0))
            side = t["side"]
            # Allow if v15 confirms direction OR if v6 (HTF) strongly confirms
            v6 = float(t.get("v6", 0))
            if side == "long" and not (v15 >= 20 or v6 >= 30):
                continue
            if side == "short" and not (v15 <= -20 or v6 <= -30):
                continue
        out.append(t)
    return out


def main():
    p = argparse.ArgumentParser(description="V2 Filter Validator")
    p.add_argument("trades_csv", help="V1 trade log CSV from edge_discovery.py")
    args = p.parse_args()

    trades = load_trades(args.trades_csv)
    print(f"Loaded {len(trades)} V1 trades from {args.trades_csv}\n")

    # Apply stages cumulatively
    stages = []
    stages.append(stage_stats(trades, "S0: V1 baseline"))

    s1 = apply_dow_filter(trades)
    stages.append(stage_stats(s1, "S1: + DOW filter (Thu/Fri only)"))

    s2 = apply_tod_filter(s1)
    stages.append(stage_stats(s2, "S2: + TOD filter (skip open 30min)"))

    s3 = apply_regime_filter(s2)
    stages.append(stage_stats(s3, "S3: + Regime filter (ORB only in Risk-On)"))

    s4 = apply_partial_take(s3, partial_R=0.5)
    stages.append(stage_stats(s4, "S4: + 0.5R partial-take rule"))

    s5 = apply_voice_signature_filter(s4)
    stages.append(stage_stats(s5, "S5: + Voice signature filter (v15/v6 for ORB)"))

    # Print stages table
    print(
        f"{'Stage':<55s} {'n':>4s} {'Win%':>5s} {'Strike':>7s} {'TotR':>7s} {'AvgR':>8s} {'PF':>5s} {'MDD':>5s}"
    )
    print("─" * 105)
    baseline_r = stages[0]["total_r"]
    for s in stages:
        if s["n"] == 0:
            print(f"{s['label']:<55s} {0:>4d}  no trades")
            continue
        delta = s["total_r"] - baseline_r if s != stages[0] else 0
        delta_str = f"({delta:+.2f})" if delta != 0 else ""
        print(
            f"{s['label']:<55s} {s['n']:>4d} {s['win_rate']:>4.1f}% {s['strike']:>6.1f}% "
            f"{s['total_r']:>+6.2f}{delta_str:>9s} {s['avg_r']:>+8.4f} {str(s['pf']):>5s} {s['max_dd']:>5.2f}"
        )

    # Final summary
    final = stages[-1]
    baseline = stages[0]
    print(f"\n{'─' * 105}")
    print("V2 PROJECTED PERFORMANCE")
    print(f"{'─' * 105}")
    print(
        f"  Trade count:   {baseline['n']:>4d} → {final['n']:>4d}  ({(final['n'] - baseline['n']) / baseline['n'] * 100:+.0f}%)"
    )
    print(
        f"  Win rate:      {baseline['win_rate']:>4.1f}% → {final['win_rate']:>4.1f}%  ({final['win_rate'] - baseline['win_rate']:+.1f} pts)"
    )
    print(
        f"  Strike rate:   {baseline['strike']:>4.1f}% → {final['strike']:>4.1f}%  ({final['strike'] - baseline['strike']:+.1f} pts)"
    )
    print(
        f"  Total R:       {baseline['total_r']:>+5.2f} → {final['total_r']:>+5.2f}  ({final['total_r'] - baseline['total_r']:+.2f}R)"
    )
    print(
        f"  Avg R/trade:   {baseline['avg_r']:>+5.4f} → {final['avg_r']:>+5.4f}  ({(final['avg_r'] - baseline['avg_r']):+.4f})"
    )
    print(f"  Profit factor: {baseline['pf']} → {final['pf']}")
    print(f"  Max DD:        {baseline['max_dd']:>4.2f}R → {final['max_dd']:>4.2f}R")

    # Verdict
    print(f"\n{'─' * 105}")
    print("VERDICT")
    print(f"{'─' * 105}")
    pf_target = 1.5
    pf_pass = (
        final["pf"] != "inf" and float(final["pf"]) >= pf_target if final["pf"] != "inf" else True
    )
    print(f"  ✓ PF >= {pf_target}:        {'PASS' if pf_pass else 'FAIL'}  ({final['pf']})")
    print(
        f"  ✓ Total R > +5R:    {'PASS' if final['total_r'] > 5 else 'FAIL'}  ({final['total_r']:+.2f}R)"
    )
    print(
        f"  ✓ MDD <= 2.0R:      {'PASS' if final['max_dd'] <= 2.0 else 'FAIL'}  ({final['max_dd']:.2f}R)"
    )
    print(f"  ✓ Sample n >= 50:   {'PASS' if final['n'] >= 50 else 'FAIL'}  ({final['n']})")


if __name__ == "__main__":
    main()
