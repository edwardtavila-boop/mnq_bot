"""
Firm Meta-Decision Layer
========================
Extends the 11-voice Firm to make SYSTEM-LEVEL decisions, not just trade calls.
The same weighted-voting architecture the Firm uses for trades now decides:

  M1: What regime are we actually in? (may disagree with rule-based detector)
  M2: What PM threshold should be used today?
  M3: Which setups should be enabled/disabled?
  M4: What's the risk budget for today?
  M5: Should we trade at all today?

Each meta-voice (MV) scores -100 to +100, weighted, PM threshold applied.
Just like the trade Firm — no single voice gets to be right alone.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import statistics
import json
import os

ET = ZoneInfo("America/New_York")


@dataclass
class MetaContext:
    """Everything the meta-Firm needs to make decisions."""
    recent_trades: List[dict] = field(default_factory=list)  # last 20 trades
    recent_decisions: List[dict] = field(default_factory=list)  # last 100 decisions
    rolling_win_rate: float = 0.0
    rolling_pf: float = 0.0
    rolling_dd: float = 0.0
    current_equity_r: float = 0.0
    peak_equity_r: float = 0.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    days_since_last_win: int = 0
    regime_history: List[str] = field(default_factory=list)  # last 20 bars
    avg_atr: float = 0.0
    avg_adx: float = 0.0
    avg_vol_z: float = 0.0
    hour_et: int = 12
    weekday: int = 1  # 1=Mon
    now_utc: Optional[datetime] = None


@dataclass
class MetaDecision:
    regime_vote: str = "NEUTRAL"
    pm_threshold: float = 30.0
    enabled_setups: List[str] = field(default_factory=lambda: ["ORB", "EMA PB", "SWEEP"])
    risk_budget_R: float = 2.0  # max loss allowed today
    size_multiplier: float = 1.0
    trade_allowed: bool = True
    reason: str = ""
    confidence: float = 0.0  # 0-100
    voices: Dict[str, float] = field(default_factory=dict)
    audit: Dict[str, str] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# META-VOICES: Each returns -100..+100, interpreted in context
# ─────────────────────────────────────────────────────────────────────────────

def mv_regime_stability(ctx: MetaContext) -> float:
    """Has the regime been stable or flip-flopping?
    Stable = high confidence; flipping = risk-off."""
    if len(ctx.regime_history) < 10:
        return 0.0
    recent = ctx.regime_history[-10:]
    mode_regime = max(set(recent), key=recent.count)
    stability = recent.count(mode_regime) / len(recent)
    if stability >= 0.9:
        return 80.0  # rock-solid regime
    if stability >= 0.7:
        return 40.0
    if stability < 0.4:
        return -60.0  # chaos, hard to trade
    return 0.0


def mv_recent_performance(ctx: MetaContext) -> float:
    """How's the system doing lately? Good run = confidence, bad run = caution."""
    if not ctx.recent_trades or len(ctx.recent_trades) < 3:
        return 0.0
    if ctx.rolling_pf >= 2.5 and ctx.rolling_win_rate >= 0.55:
        return 70.0
    if ctx.rolling_pf >= 1.8:
        return 40.0
    if ctx.rolling_pf < 0.8:
        return -70.0  # system is bleeding
    if ctx.rolling_pf < 1.0:
        return -40.0
    return 0.0


def mv_drawdown_check(ctx: MetaContext) -> float:
    """How deep are we in drawdown from equity peak?"""
    dd = ctx.peak_equity_r - ctx.current_equity_r
    if dd >= 3.0:
        return -80.0  # serious, cut size
    if dd >= 2.0:
        return -50.0
    if dd >= 1.0:
        return -20.0
    if dd <= 0.3 and ctx.peak_equity_r > 2.0:
        return 40.0  # near or at new highs
    return 0.0


def mv_streak_detector(ctx: MetaContext) -> float:
    """Consecutive wins/losses tell us momentum state."""
    if ctx.consecutive_losses >= 3:
        return -70.0  # 3 losses = something's wrong, pause or cut size
    if ctx.consecutive_losses >= 2:
        return -40.0
    if ctx.consecutive_wins >= 4:
        return -30.0  # 4 wins = mean reversion risk, don't get greedy
    if ctx.consecutive_wins >= 2 and ctx.consecutive_wins <= 3:
        return 30.0  # healthy streak
    return 0.0


def mv_volatility_regime(ctx: MetaContext) -> float:
    """Current vol state. Low ATR = ranging (bad for trend). Huge ATR = chaos."""
    # Interpret ATR relative to recent history via vol_z proxy
    if ctx.avg_vol_z > 2.5:
        return -50.0  # extreme vol, chop likely
    if ctx.avg_vol_z > 1.5:
        return 30.0  # elevated but tradeable
    if ctx.avg_vol_z < -1.0:
        return -40.0  # dead market
    if ctx.avg_adx >= 25:
        return 50.0  # strong trending
    if ctx.avg_adx < 15:
        return -30.0  # weak trend, range-bound
    return 10.0


def mv_time_of_day(ctx: MetaContext) -> float:
    """ET clock. Power hours get boost, lunch gets penalty."""
    h = ctx.hour_et
    if 9 <= h < 11:
        return 60.0  # morning power hour
    if 14 <= h < 16:
        return 40.0  # afternoon power hour
    if 11 <= h < 14:
        return -30.0  # lunch chop
    return 0.0  # overnight/off-hours


def mv_day_of_week(ctx: MetaContext) -> float:
    """Mon-Wed tend strongest. Thu-Fri weaker for trend setups."""
    d = ctx.weekday
    if d == 1 or d == 2:  # Mon, Tue
        return 50.0
    if d == 3:  # Wed
        return 30.0
    if d == 4:  # Thu - v1 data showed this was worst
        return -40.0
    if d == 5:  # Fri
        return -20.0
    return 0.0


def mv_correlation_agreement(ctx: MetaContext) -> float:
    """Across recent decisions, do the intermarket voices agree with NQ action?
    If VIX+ES+DXY+TICK all agree with NQ direction, high confidence."""
    if not ctx.recent_decisions:
        return 0.0
    agreements = 0
    total = 0
    for d in ctx.recent_decisions[-20:]:
        voices = d.get("voices", {})
        direction = 1 if d.get("pm_final", 0) > 0 else -1 if d.get("pm_final", 0) < 0 else 0
        if direction == 0:
            continue
        intermkt_voices = [voices.get(k, 0) for k in ("v8", "v9", "v10", "v11")]
        active = [v for v in intermkt_voices if v != 0]
        if not active:
            continue
        total += 1
        agree = sum(1 for v in active if (v > 0) == (direction > 0))
        if agree / len(active) >= 0.6:
            agreements += 1
    if total == 0:
        return 0.0
    ratio = agreements / total
    if ratio >= 0.75:
        return 60.0
    if ratio >= 0.5:
        return 20.0
    if ratio < 0.3:
        return -50.0  # intermarket disagrees often = bad environment
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# META-FIRM: Aggregate voices into system-level decisions
# ─────────────────────────────────────────────────────────────────────────────

# Default meta-voice weights (can be overridden by learned weights)
META_WEIGHTS = {
    "regime_stability":    1.2,
    "recent_performance":  1.5,
    "drawdown_check":      1.8,  # drawdown is critical
    "streak_detector":     1.0,
    "volatility_regime":   1.2,
    "time_of_day":         0.8,
    "day_of_week":         1.0,
    "correlation_agreement": 1.1,
}


def run_meta_firm(ctx: MetaContext, base_pm: float = 30.0) -> MetaDecision:
    """Run all meta-voices and produce a MetaDecision."""
    voices = {
        "regime_stability":      mv_regime_stability(ctx),
        "recent_performance":    mv_recent_performance(ctx),
        "drawdown_check":        mv_drawdown_check(ctx),
        "streak_detector":       mv_streak_detector(ctx),
        "volatility_regime":     mv_volatility_regime(ctx),
        "time_of_day":           mv_time_of_day(ctx),
        "day_of_week":           mv_day_of_week(ctx),
        "correlation_agreement": mv_correlation_agreement(ctx),
    }

    # Weighted aggregate
    total_w = sum(META_WEIGHTS.values())
    confidence_raw = sum(voices[k] * META_WEIGHTS[k] for k in voices) / total_w
    # Normalize to 0-100 (confidence scale)
    confidence = max(0.0, min(100.0, 50.0 + confidence_raw * 0.5))

    # Derived decisions
    dec = MetaDecision(voices=voices, confidence=round(confidence, 1))

    # 1. Regime vote: weighted agreement from stability + volatility
    if voices["volatility_regime"] >= 40 and voices["regime_stability"] >= 40:
        dec.regime_vote = "RISK-ON"
    elif voices["volatility_regime"] <= -40 or voices["regime_stability"] <= -40:
        dec.regime_vote = "RISK-OFF"
    else:
        dec.regime_vote = "NEUTRAL"
    dec.audit["regime_vote"] = (
        f"vol_regime={voices['volatility_regime']:+.0f}, "
        f"stability={voices['regime_stability']:+.0f} → {dec.regime_vote}"
    )

    # 2. PM threshold vote: start from base, adjust by confidence
    # High confidence = lower PM (more trades). Low confidence = raise PM (stricter).
    pm_adjustment = -(confidence - 50) * 0.3  # ±15 range from base
    dec.pm_threshold = round(max(20.0, min(50.0, base_pm + pm_adjustment)), 1)
    dec.audit["pm_threshold"] = f"base={base_pm}, conf={confidence:.1f} → PM={dec.pm_threshold}"

    # 3. Which setups enabled? Disable setups based on unfavorable conditions
    enabled = ["ORB", "EMA PB", "SWEEP"]
    if voices["day_of_week"] <= -30 and "EMA PB" in enabled:
        # Thu/Fri = skip EMA PB per v1 wisdom
        enabled.remove("EMA PB")
        dec.audit["skip_ema"] = f"dow voice {voices['day_of_week']:+.0f}"
    if voices["volatility_regime"] <= -40 and "SWEEP" in enabled:
        # Low vol = sweep setups don't work (no sweeps happen)
        enabled.remove("SWEEP")
        dec.audit["skip_sweep"] = f"vol_regime {voices['volatility_regime']:+.0f}"
    if voices["time_of_day"] <= -20 and len(enabled) > 1:
        # Lunch = only keep best-performing setup, skip weaker
        if "SWEEP" in enabled:
            enabled.remove("SWEEP")
            dec.audit["lunch_skip"] = "lunch chop"
    dec.enabled_setups = enabled

    # 4. Risk budget for today (max loss allowed)
    if voices["drawdown_check"] <= -60:
        dec.risk_budget_R = 1.0  # cut budget in deep DD
        dec.audit["risk_cut"] = "deep DD, budget=1R"
    elif voices["drawdown_check"] <= -40:
        dec.risk_budget_R = 1.5
        dec.audit["risk_cut"] = "DD, budget=1.5R"
    elif voices["recent_performance"] >= 50:
        dec.risk_budget_R = 2.5  # hot streak, allow more
        dec.audit["risk_boost"] = "good perf, budget=2.5R"
    else:
        dec.risk_budget_R = 2.0

    # 5. Size multiplier
    if voices["streak_detector"] <= -60:
        dec.size_multiplier = 0.5  # 3-loss streak = half size
        dec.audit["size_cut"] = "3 losses, half size"
    elif voices["drawdown_check"] <= -50:
        dec.size_multiplier = 0.75
    elif voices["recent_performance"] >= 50 and voices["drawdown_check"] >= 0:
        dec.size_multiplier = 1.0  # don't go over 1x, discipline
    else:
        dec.size_multiplier = 1.0

    # 6. Trade allowed at all?
    # Hard stops: deep DD + losing streak = pause
    if voices["drawdown_check"] <= -70 and voices["streak_detector"] <= -60:
        dec.trade_allowed = False
        dec.reason = f"PAUSE: deep DD + loss streak (dd={voices['drawdown_check']:+.0f}, streak={voices['streak_detector']:+.0f})"
    elif confidence < 20:
        dec.trade_allowed = False
        dec.reason = f"PAUSE: meta-confidence too low ({confidence:.0f})"
    else:
        dec.trade_allowed = True
        dec.reason = f"TRADE: meta-confidence {confidence:.0f}/100, {len(dec.enabled_setups)} setups active"

    return dec


def save_meta_decision(decision: MetaDecision, path: str):
    """Persist a meta-decision to JSON for audit trail."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "regime_vote": decision.regime_vote,
        "pm_threshold": decision.pm_threshold,
        "enabled_setups": decision.enabled_setups,
        "risk_budget_R": decision.risk_budget_R,
        "size_multiplier": decision.size_multiplier,
        "trade_allowed": decision.trade_allowed,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "voices": decision.voices,
        "audit": decision.audit,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def load_recent_meta(dir_path: str, n: int = 10) -> List[dict]:
    """Load last N meta-decisions from audit trail."""
    if not os.path.isdir(dir_path):
        return []
    files = sorted([f for f in os.listdir(dir_path) if f.endswith(".json")])[-n:]
    out = []
    for f in files:
        try:
            with open(os.path.join(dir_path, f)) as fh:
                out.append(json.load(fh))
        except Exception:
            continue
    return out
