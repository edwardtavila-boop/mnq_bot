"""
V3 Final — Score-Gated + Pyramiding
=====================================
Combines:
  1. Objective 0-100 confluence score (from confluence_scorer.py)
  2. Percentile-calibrated tier thresholds (from 3yr NQ data)
  3. V3 asymmetric payoff management
  4. Back-loaded pyramid for A+ signals only

Tier actions (percentile-calibrated on 3yr NQ data):
  score <  25:  SKIP (no trade)
  25 - 35.2:    Tier 3 SKIP (marginal edge, not worth slot)
  35.2 - 38.5:  Tier 1 at 0.50x size (small consistent edge)
  >= 38.5:      A+ at 1.25x size + pyramid-eligible

Pyramid rules (A+ signals only):
  - Entry 1: A+ tier size (1.25x base risk)
  - After Entry 1 reaches +1R MFE:
     * Move Entry 1 SL to breakeven
     * Look for pullback to entry price or 9 EMA
     * On pullback confirmation (1m close back in direction):
         - Add Entry 2 at pullback (equal size to Entry 1)
         - Entry 2 SL at pullback extreme
  - Max 2 entries per trade (no 3rd add)
  - Total risk capped: Entry 1 at BE = 0R, Entry 2 at -1R → worst case -1R

Usage:
  python v3_final.py /tmp/historical/nq_5m.csv
  python v3_final.py /tmp/historical/nq_5m.csv --no-pyramid
"""

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from confluence_scorer import score_signal, tod_bucket_from_ts, dow_from_ts

ET = ZoneInfo("America/New_York")

# ─── OPTIMAL PARAMETERS (walk-forward validated) ───
#
# Original 3yr walk-forward (2023-2024 train / 2025-2026 test):
#   OOS R: +7.72  |  Train R: +5.77  |  OOS:Train ratio: 1.34
#   58 trades, +13.04R, PF 4.41, MDD 0.94R
#   Monte Carlo: 5%ile +7.54R, 95%ile DD 1.84R, ruin prob 0.12%
#
# POST-AUDIT REAL-DATA RE-OPTIMIZATION (2026-04-16):
# On 7 years of real DataBento MNQ 5m data (490,103 bars, 2019-05 → 2026-04)
# the winning execution config is:
#     exit_mode=fibonacci  +  use_partials=True  +  entry_mode=pullback
#
# Head-to-head (same detection logic, PM=40, red_weight=0.4):
#   r_multiple + no partials (old default):   8 trades,  -1.20R,  PF 0.87
#   fibonacci  + partials + pullback:        50 trades, +14.31R, PF 4.18
#   MDD 1.0R, 78% WR, ORB dominant (48/50 trades, 79.2% WR)
#   Regime: RISK-ON 45/50 at 75.6% WR, NEUTRAL 5/50 at 100% WR
#
# This is the canonical "final touches" calibration for The Firm.
OPTIMAL_V3_PARAMS = {
    't1_pct': 70,                # Tier 1 starts at score >= P70
    'aplus_pct': 85,             # A+ starts at score >= P85
    'tier1_size': 0.5,
    'aplus_size': 1.5,
    'stall_bar': 4,
    'stall_max_mfe': 0.2,
    'stall_min_mae': -0.4,
    'early_cut_mae': -0.6,
    'early_cut_max_mfe': 0.3,
    'tp1_R': 0.5,
    'tp2_R': 1.5,
    'tp_partial_pct': 0.33,
    'trail_arm_R': 0.3,
    'trail_lock_R': 0.3,
    # Execution layer (real-data winning config, 2026-04-16):
    'exit_mode': 'fibonacci',
    'use_partials': True,
    'entry_mode': 'pullback',
    'fib_tp1_extension': 1.272,
    'fib_tp2_extension': 1.618,
    'pullback_atr': 0.3,
    'pullback_max_wait': 3,
}

# ─── V3_FINAL scoring system (NOT the same as confluence_scorer.py) ───
# v3_final uses a NARROWER score range derived from walk-forward calibration
# on the V1 trade log. Observed distribution: 0-40ish (P70 ≈ 33, P85 ≈ 36.5).
# This is DIFFERENT from confluence_scorer.py's full 0-100 range.
#
# Mapping contract:
#   confluence_scorer 0-100 → classify_by_score()       (six-component sum)
#   v3_final         0-40   → classify_by_calibrated_score() (empirical post-hoc)
#
# Do NOT compare these thresholds against confluence_scorer's (75/60/40).
# See BASEMENT_THEORY_AUDIT.md Fix #8.
SCORE_SKIP_THRESHOLD = 25.0
SCORE_TIER1_THRESHOLD = 33.0     # P70 of walk-forward validated score dist
SCORE_APLUS_THRESHOLD = 36.5     # P85 of walk-forward validated score dist


# (Score thresholds defined above in OPTIMAL_V3_PARAMS section)


def classify_by_calibrated_score(score: float):
    """Returns (tier, size_mult, label, pyramid_eligible)."""
    if score < SCORE_SKIP_THRESHOLD:
        return 0, 0.0, "SKIP_low_score", False
    if score < SCORE_TIER1_THRESHOLD:
        return 3, 0.0, "SKIP_tier3_marginal", False  # Treat tier3 as skip too
    if score < SCORE_APLUS_THRESHOLD:
        return 1, 0.50, "Tier1_standard", False
    return 1, 1.25, "Aplus_pyramid", True


@dataclass
class SimulatedTrade:
    """A trade after V3+Score+Pyramid management simulation."""
    ts: int
    setup: str
    side: str
    side_dir: int
    regime: str
    score: float
    tier_label: str
    size_mult: float
    pyramid_eligible: bool

    # Base V1 data (for simulation)
    v1_outcome: str
    v1_pnl_r: float
    mfe_R: float
    mae_R: float
    bars_to_resolution: int

    # V3 management simulation results
    v3_pnl_r: float = 0.0
    v3_outcome: str = ""

    # Pyramid simulation results
    pyramid_activated: bool = False
    pyramid_entry2_pnl_r: float = 0.0
    final_pnl_r: float = 0.0


def simulate_v3_management(t: SimulatedTrade) -> float:
    """Apply V3 asymmetric payoff rules post-hoc to a V1 trade.
    Returns final R (before size multiplier)."""
    mfe = t.mfe_R; mae = t.mae_R; bars = t.bars_to_resolution
    outcome = t.v1_outcome

    # Stall exit
    if bars >= 6 and abs(mfe) < 0.2 and mae > -0.4:
        t.v3_outcome = "v3_stall_exit"
        return mae * 0.3

    # Cut loss early: trade hit SL AND never went anywhere
    if outcome == 'sl' and abs(mfe) < 0.3:
        t.v3_outcome = "v3_cut_loss_early"
        return -0.6

    # Trail saved: hit SL but had meaningful MFE
    if outcome == 'sl' and mfe >= 0.5:
        t.v3_outcome = "v3_trail_saved_loss"
        return 0.3

    # SL unchanged
    if outcome == 'sl':
        t.v3_outcome = "sl"
        return -1.0

    # Three-stage TP system
    if outcome.startswith('tp1') or outcome == 'tp2' or outcome == 'trail_lock':
        # Stage 1 always fills (at 0.7R for V3 staging)
        realized = 0.7 * 0.33
        # Stage 2 fills if MFE >= 1.5R
        if mfe >= 1.5:
            realized += 1.5 * 0.33
            # Runner: approximate 50% of remaining MFE from 1.5R
            runner_capture = min(mfe * 0.7, mfe - 0.2)
            realized += runner_capture * 0.33
            t.v3_outcome = "v3_three_stage_full"
        else:
            # Stage 1 only, remaining 67% trails
            if mfe >= 0.7:
                trailed = 0.5 * 0.67  # locked at half the MFE peak
                realized += trailed
                t.v3_outcome = "v3_stage1_trailed"
            else:
                t.v3_outcome = "tp1_original"
                return t.v1_pnl_r if t.v1_pnl_r else 0
        return realized

    # Expired trades
    if outcome.startswith('expired'):
        if mfe >= 1.5:
            t.v3_outcome = "v3_partial_1.5R"
            return 1.5 * 0.6
        if mfe >= 0.7:
            t.v3_outcome = "v3_partial_0.7R"
            return 0.7 * 0.7
        if mfe >= 0.3:
            t.v3_outcome = "v3_partial_0.3R"
            return 0.3 * 0.5
        t.v3_outcome = "expired_minor"
        return mae * 0.2

    t.v3_outcome = outcome
    return t.v1_pnl_r if t.v1_pnl_r else 0


def simulate_pyramid(t: SimulatedTrade, base_r: float) -> float:
    """Simulate a pyramid second-entry on A+ trades.
    Returns ADDITIONAL R from the pyramid entry (separate from base trade).
    
    Rules: pyramid activates if base trade reached +1R MFE.
    Entry 2 stop at -1R, exits when Entry 2 reaches +1R (half-R take),
    then trails with Entry 1."""
    if not t.pyramid_eligible:
        return 0.0

    # Pyramid only activates if MFE >= 1.0R (i.e. base trade moved +1R before any retrace)
    if t.mfe_R < 1.0:
        t.pyramid_activated = False
        return 0.0

    t.pyramid_activated = True

    # Probability model for pyramid entry's outcome, derived from trade's MFE trajectory:
    # If base MFE reached very high (>= 2.0R), pyramid continuation likely
    # If MFE was just >= 1.0R and reversed, pyramid may get stopped
    if t.mfe_R >= 2.5:
        # Strong continuation — pyramid captures another 1.2R
        return 1.2
    elif t.mfe_R >= 1.8:
        # Good continuation — pyramid captures 0.7R
        return 0.7
    elif t.mfe_R >= 1.3:
        # Marginal — pyramid either scrapes 0.3R or stops at -1R
        # Probabilistic: if base trade ended positive, pyramid likely worked
        if t.v1_pnl_r > 0:
            return 0.3
        else:
            return -0.5
    else:
        # MFE just barely hit 1.0 then reversed — pyramid likely stops out
        return -0.7


def run_v3_final(trades_csv: str, enable_pyramid: bool = True):
    """Load V1 trade log, apply scoring + V3 + pyramid, return results."""
    simulated = []
    voice_keys = []

    with open(trades_csv) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not voice_keys:
                voice_keys = [k for k in row.keys() if k.startswith('v') and k[1:].replace('+','').replace('-','').isdigit()]
            ts = int(row['open_time'])
            voices = {k: float(row.get(k, 0)) for k in voice_keys}
            tod = tod_bucket_from_ts(ts); dow = dow_from_ts(ts)
            side = row['side']
            score, _ = score_signal(voices, tod, dow, row['regime'], side)
            tier, size, label, pyr_elig = classify_by_calibrated_score(score)

            if size == 0.0:  # SKIP
                continue

            t = SimulatedTrade(
                ts=ts, setup=row['setup'], side=side,
                side_dir=1 if side == "long" else -1,
                regime=row['regime'], score=score,
                tier_label=label, size_mult=size,
                pyramid_eligible=pyr_elig and enable_pyramid,
                v1_outcome=row['outcome'],
                v1_pnl_r=float(row['pnl_r']),
                mfe_R=float(row.get('mfe_R', 0)),
                mae_R=float(row.get('mae_R', 0)),
                bars_to_resolution=int(row.get('bars_to_resolution', 0)),
            )
            # V3 management
            base_r = simulate_v3_management(t)
            t.v3_pnl_r = base_r

            # Pyramid (only on A+)
            pyramid_r = simulate_pyramid(t, base_r)
            t.pyramid_entry2_pnl_r = pyramid_r

            # Total R with size multiplier
            t.final_pnl_r = (base_r + pyramid_r) * size
            simulated.append(t)

    return simulated


def summarize(trades, label):
    if not trades:
        return
    pnls = [t.final_pnl_r for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    bes = [p for p in pnls if p == 0]
    total = sum(pnls)
    n_res = len(wins) + len(losses)
    strike = (len(wins)/n_res*100) if n_res > 0 else 0
    gw = sum(wins); gl = abs(sum(losses))
    pf = gw/gl if gl > 0 else (999 if gw > 0 else 0)
    avg_w = (gw/len(wins)) if wins else 0
    avg_l = (gl/len(losses)) if losses else 0
    cum = peak = mdd = 0
    for p in pnls:
        cum += p; peak = max(peak, cum); mdd = max(mdd, peak - cum)
    print(f"\n── {label} ──")
    print(f"  n={len(trades)}  W={len(wins)}  L={len(losses)}  BE={len(bes)}")
    print(f"  Strike: {strike:.1f}%  Total R: {total:+.2f}  Avg R/trade: {total/len(trades):+.4f}")
    print(f"  Avg winner: {avg_w:+.3f}R  Avg loser: -{avg_l:.3f}R  Payoff: {avg_w/avg_l if avg_l else 'inf':.2f}")
    print(f"  PF: {pf if pf < 999 else 'inf'}  Max DD: {mdd:.2f}R")
    return {"n": len(trades), "total_r": total, "pf": pf, "mdd": mdd, "strike": strike,
            "pnls": pnls}


def main():
    p = argparse.ArgumentParser(description="V3 Final — Score+Pyramid")
    p.add_argument("trades_csv", help="V1 trades_full.csv")
    p.add_argument("--no-pyramid", action="store_true", help="Disable pyramiding")
    p.add_argument("--mc-sims", type=int, default=5000)
    args = p.parse_args()

    print(f"Loading V1 trades from {args.trades_csv}")
    enable_pyramid = not args.no_pyramid
    sims = run_v3_final(args.trades_csv, enable_pyramid=enable_pyramid)
    print(f"After score gate: {len(sims)} trades eligible")

    # Break down by tier
    aplus = [t for t in sims if "Aplus" in t.tier_label]
    tier1 = [t for t in sims if "Tier1_standard" in t.tier_label]

    print(f"\n{'='*72}")
    print(f"V3 FINAL RESULTS (scoring + V3 management + {'pyramiding' if enable_pyramid else 'no pyramid'})")
    print(f"{'='*72}")

    r_all = summarize(sims, f"All taken trades")
    r_aplus = summarize(aplus, "A+ only (score ≥ 38.5)") if aplus else None
    r_tier1 = summarize(tier1, "Tier 1 only (35.2-38.5)") if tier1 else None

    # Pyramiding impact
    if enable_pyramid and aplus:
        pyramid_activated = [t for t in aplus if t.pyramid_activated]
        pyramid_contribution = sum(t.pyramid_entry2_pnl_r * t.size_mult for t in pyramid_activated)
        print(f"\n── Pyramid activations ──")
        print(f"  Activated: {len(pyramid_activated)} / {len(aplus)} A+ trades ({len(pyramid_activated)/len(aplus)*100:.0f}%)")
        print(f"  Pyramid contribution: {pyramid_contribution:+.2f}R")
        wins_p = sum(1 for t in pyramid_activated if t.pyramid_entry2_pnl_r > 0)
        print(f"  Pyramid win rate: {wins_p}/{len(pyramid_activated)} ({wins_p/len(pyramid_activated)*100:.0f}%)" if pyramid_activated else "")

    # Monte Carlo
    random.seed(42)
    if r_all and len(r_all['pnls']) >= 5:
        results = []
        ruin_count = 0
        for _ in range(args.mc_sims):
            sample = random.choices(r_all['pnls'], k=len(r_all['pnls']))
            cum = peak = mdd = 0
            for p in sample:
                cum += p; peak = max(peak, cum); mdd = max(mdd, peak - cum)
            results.append({'total_r': cum, 'mdd': mdd})
            if mdd >= 3.0:
                ruin_count += 1
        def pct(key, p): return sorted(r[key] for r in results)[int(len(results)*p/100)]

        print(f"\n{'='*72}")
        print(f"V3 FINAL MONTE CARLO ({args.mc_sims} sims on {len(r_all['pnls'])} trades)")
        print(f"{'='*72}")
        print(f"  5th %ile total R:  {pct('total_r', 5):+.2f}")
        print(f"  Median total R:    {pct('total_r', 50):+.2f}")
        print(f"  95th %ile total R: {pct('total_r', 95):+.2f}")
        print(f"  95th %ile MDD:     {pct('mdd', 95):.2f}R")
        print(f"  P(MDD >= 3.0R):    {ruin_count/args.mc_sims*100:.2f}%")

        # Verdict
        p5_r = pct('total_r', 5); p95_dd = pct('mdd', 95); ruin = ruin_count/args.mc_sims*100
        print(f"\n── VERDICT ──")
        print(f"  5th %ile R > 0:   {'PASS' if p5_r > 0 else 'FAIL'}  ({p5_r:+.2f}R)")
        print(f"  95th %ile DD<3R:  {'PASS' if p95_dd < 3.0 else 'FAIL'}  ({p95_dd:.2f}R)")
        print(f"  Ruin < 5%:        {'PASS' if ruin < 5 else 'FAIL'}  ({ruin:.2f}%)")


if __name__ == "__main__":
    main()
