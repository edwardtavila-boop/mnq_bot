"""
Confluence Scorer — Objective 0-100 Signal Strength
====================================================
Replaces V3's day-of-week / time-of-day hardcoded tier rules with an
objective scoring system. Every component draws from the Firm's voice
scores (already computed) — no subjective "this feels strong" input.

Score components (total 100 points):
  Structure alignment        (0-20): v6 HTF + v2 EMA + aligned direction
  Liquidity context          (0-15): v3 Sweep + v14 P/D + v7 Liq Vacuum
  Volume confirmation        (0-15): v4 VWAP + v5 Momentum magnitude
  Time/session edge          (0-15): v13 Killzone + TOD bucket quality
  Intermarket confirmation   (0-15): v9 ES corr + v8 VIX + v11 TICK
  Edge stack (microstructure)(0-20): v12 Cum Delta + v15 FVG

Tier mapping (data-derived from edge_discovery findings):
  Score <  40 : SKIP (no edge detected)
  40 - 60    : Tier 3 (0.25x size, speculative)
  60 - 75    : Tier 2 (0.50x size, standard)
  75 - 90    : Tier 1 (1.00x size, premium)
  90+        : A+ mode (1.25x size, pyramid-eligible)

Usage:
  from confluence_scorer import score_signal, classify_by_score
  score, components = score_signal(voices, tod_bucket, dow, regime, side)
  tier, size, label = classify_by_score(score)
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import Dict, Tuple

ET = ZoneInfo("America/New_York")


@dataclass
class ScoreComponents:
    """Breakdown of the 0-100 confluence score by category."""
    structure: float = 0.0      # 0-20
    liquidity: float = 0.0      # 0-15
    volume: float = 0.0         # 0-15
    time_session: float = 0.0   # 0-15
    intermarket: float = 0.0    # 0-15
    edge_stack: float = 0.0     # 0-20
    total: float = 0.0          # 0-100

    def as_dict(self):
        return {
            "structure": round(self.structure, 1),
            "liquidity": round(self.liquidity, 1),
            "volume": round(self.volume, 1),
            "time_session": round(self.time_session, 1),
            "intermarket": round(self.intermarket, 1),
            "edge_stack": round(self.edge_stack, 1),
            "total": round(self.total, 1),
        }


def _voice(voices: Dict[str, float], key: str) -> float:
    """Safely fetch a voice score, default 0."""
    return voices.get(key, 0.0)


def _signed_bonus(voice_val: float, side: str, max_pts: float = 5.0) -> float:
    """Reward voices that agree with trade direction.
    Positive voice value + long signal = bonus. Negative voice + short = bonus."""
    signal_dir = 1 if side == "long" else -1
    # Voice value in [-100, +100]. Aligned value reward proportional to magnitude.
    aligned = voice_val * signal_dir
    if aligned <= 0:
        return 0.0
    # 100 -> max_pts, proportional
    return min(max_pts, max_pts * aligned / 100.0)


def score_structure(voices, side) -> float:
    """0-20 points for higher/lower TF structure alignment."""
    pts = 0.0
    pts += _signed_bonus(_voice(voices, "v6"), side, max_pts=8.0)   # HTF bias
    pts += _signed_bonus(_voice(voices, "v2"), side, max_pts=7.0)   # EMA pullback/trend
    pts += _signed_bonus(_voice(voices, "v1"), side, max_pts=5.0)   # Primary ORB
    return min(20.0, pts)


def score_liquidity(voices, side) -> float:
    """0-15 points for liquidity context."""
    pts = 0.0
    pts += _signed_bonus(_voice(voices, "v3"), side, max_pts=7.0)   # Sweep+Reclaim
    pts += _signed_bonus(_voice(voices, "v14"), side, max_pts=4.0)  # Premium/Discount
    pts += _signed_bonus(_voice(voices, "v7"), side, max_pts=4.0)   # Liq Vacuum
    return min(15.0, pts)


def score_volume(voices, side) -> float:
    """0-15 points for volume confirmation."""
    pts = 0.0
    pts += _signed_bonus(_voice(voices, "v5"), side, max_pts=8.0)   # Momentum (incl volume)
    pts += _signed_bonus(_voice(voices, "v4"), side, max_pts=7.0)   # VWAP MR
    return min(15.0, pts)


def score_time_session(voices, tod_bucket, dow) -> float:
    """0-15 points for time-of-day and day-of-week edge.
    Data-derived weights from edge_discovery findings."""
    pts = 0.0

    # Killzone voice
    pts += max(0, _voice(voices, "v13")) / 100.0 * 5.0  # 0-5 pts

    # TOD bucket weights (from edge_discovery: 3yr NQ data)
    tod_weights = {
        "lunch":       6.0,   # +2.40R total, PF inf - strongest
        "mid_am":      5.0,   # +2.15R, PF 3.15
        "moc":         4.0,   # +1.20R, PF inf
        "power_hour":  2.5,   # +0.05R, marginal
        "early_pm":    1.0,   # -0.40R, weak
        "open_30min":  0.0,   # -8.05R - disaster zone gets zero
        "premarket":   0.0,
        "after_hours": 0.0,
        "weekend":     0.0,
    }
    pts += tod_weights.get(tod_bucket, 0.0)

    # DOW weights (from edge_discovery: 3yr NQ data)
    dow_weights = {
        "Thu": 4.0,   # +4.10R, PF 2.03 - best day
        "Fri": 2.0,   # +0.30R, PF 1.04
        "Wed": 1.0,   # -1.60R
        "Tue": 0.0,   # -2.40R
        "Mon": 0.0,   # -3.05R - worst day
        "Sat": 0.0, "Sun": 0.0,
    }
    pts += dow_weights.get(dow, 0.0)

    return min(15.0, pts)


def score_intermarket(voices, side) -> float:
    """0-15 points for intermarket confirmation."""
    pts = 0.0
    pts += _signed_bonus(_voice(voices, "v9"), side, max_pts=7.0)   # ES correlation
    pts += _signed_bonus(_voice(voices, "v8"), side, max_pts=4.0)   # VIX
    pts += _signed_bonus(_voice(voices, "v11"), side, max_pts=4.0)  # TICK
    return min(15.0, pts)


def score_edge_stack(voices, side) -> float:
    """0-20 points for microstructure edge stack."""
    pts = 0.0
    pts += _signed_bonus(_voice(voices, "v15"), side, max_pts=10.0)  # FVG
    pts += _signed_bonus(_voice(voices, "v12"), side, max_pts=10.0)  # Cum Delta
    return min(20.0, pts)


def score_signal(voices: Dict[str, float], tod_bucket: str, dow: str,
                 regime: str, side: str) -> Tuple[float, ScoreComponents]:
    """Compute objective 0-100 signal strength score.
    Returns (total_score, component_breakdown)."""
    c = ScoreComponents()
    c.structure = score_structure(voices, side)
    c.liquidity = score_liquidity(voices, side)
    c.volume = score_volume(voices, side)
    c.time_session = score_time_session(voices, tod_bucket, dow)
    c.intermarket = score_intermarket(voices, side)
    c.edge_stack = score_edge_stack(voices, side)
    # Explicit cap to bound total in canonical [0, 100] range even if
    # component math overflows due to upstream bugs. Protects sizing logic.
    # See BASEMENT_THEORY_AUDIT.md Fix #5.
    c.total = min(100.0, max(0.0,
        c.structure + c.liquidity + c.volume + c.time_session + c.intermarket + c.edge_stack
    ))
    return c.total, c


def classify_by_score(score: float) -> Tuple[int, float, str]:
    """Map 0-100 score to (tier, size_mult, label).
    Tier 0 = skip (score < 40)."""
    if score < 40:
        return 0, 0.0, "SKIP: no confluence"
    if score < 60:
        return 3, 0.25, "Tier3 speculative"
    if score < 75:
        return 2, 0.50, "Tier2 standard"
    if score < 90:
        return 1, 1.00, "Tier1 premium"
    return 1, 1.25, "A+ pyramid-eligible"


def tod_bucket_from_ts(ts: int) -> str:
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


def dow_from_ts(ts: int) -> str:
    et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)
    return ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][et.weekday()]


# ─── Validation: run on V1 3-year trade log to see score distribution ───
def validate_on_trade_log(csv_path: str):
    """Apply scorer to the V1 3yr trade log and show how scores correlate with outcomes."""
    import csv
    trades = []
    voice_keys = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not voice_keys:
                voice_keys = [k for k in row.keys() if k.startswith('v') and k[1:].replace('+','').replace('-','').isdigit()]
            trades.append(row)

    by_tier = {0: [], 1: [], 2: [], 3: []}
    for t in trades:
        ts = int(t['open_time'])
        voices = {k: float(t.get(k, 0)) for k in voice_keys}
        tod = tod_bucket_from_ts(ts)
        dow = dow_from_ts(ts)
        score, comps = score_signal(voices, tod, dow, t['regime'], t['side'])
        tier, size, label = classify_by_score(score)
        by_tier[tier].append({
            'trade': t, 'score': score, 'tier': tier, 'size': size,
            'pnl_r': float(t['pnl_r']),
        })

    print(f"\n{'='*72}")
    print(f"OBJECTIVE SCORER VALIDATION on {len(trades)} V1 trades")
    print(f"{'='*72}")
    print(f"{'Tier':<6s} {'Label':<25s} {'n':>5s} {'Wins':>5s} {'Losses':>7s} {'TotR':>8s} {'AvgR':>8s} {'Str%':>6s}")
    for tier in [1, 2, 3, 0]:
        ts = by_tier[tier]
        if not ts:
            continue
        wins = sum(1 for t in ts if t['pnl_r'] > 0)
        losses = sum(1 for t in ts if t['pnl_r'] < 0)
        total_r = sum(t['pnl_r'] for t in ts)
        avg_r = total_r / len(ts) if ts else 0
        n_res = wins + losses
        strike = (wins / n_res * 100) if n_res > 0 else 0
        labels = {1: "Tier1+ premium/A+", 2: "Tier2 standard",
                  3: "Tier3 speculative", 0: "SKIP (score<40)"}
        print(f"  {tier:<4d} {labels[tier]:<25s} {len(ts):>5d} {wins:>5d} {losses:>7d} "
              f"{total_r:>+7.2f} {avg_r:>+8.4f} {strike:>5.1f}%")

    # Score distribution
    all_scores = [s for tier_list in by_tier.values() for s in (t['score'] for t in tier_list)]
    if all_scores:
        print(f"\nScore distribution:")
        print(f"  Min: {min(all_scores):.1f}  Max: {max(all_scores):.1f}  Mean: {sum(all_scores)/len(all_scores):.1f}")
        buckets = [(0, 40, 'SKIP'), (40, 60, 'T3'), (60, 75, 'T2'), (75, 90, 'T1'), (90, 200, 'A+')]
        for lo, hi, lbl in buckets:
            count = sum(1 for s in all_scores if lo <= s < hi)
            bar = "█" * int(count / max(1, max(all_scores)) * 40)
            print(f"  {lbl:4s} [{lo:>3d}-{hi:<3d}]: {count:>4d}  {bar}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        validate_on_trade_log(sys.argv[1])
    else:
        print("Usage: python confluence_scorer.py <trades_full.csv>")
