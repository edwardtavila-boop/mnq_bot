"""
Apex v2 Firm Engine
===================
Pure-Python implementation of the Firm framework that mirrors the Pine Script.
Used by the backtest engine and the live webhook server so that decisions made
on historical bars match exactly what Pine would produce on the same bars.

The engine takes a stream of bars and computes:
  - 7 voice scores (-100 to +100)
  - regime (RISK-ON / RISK-OFF / NEUTRAL / CRISIS)
  - red team score (0-100)
  - PM final score (|quant total| - red team penalty * regime weight)
  - fire decision (PM final >= threshold)

Voices V1-V3 are setup-tied (need setup signals fed in from outside).
Voices V4-V7 are computed purely from price/indicators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence
import math


@dataclass
class Bar:
    """OHLCV bar with optional pre-computed indicators."""
    time: int          # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    # Optional pre-computed (filled by IndicatorState if not provided)
    atr: Optional[float] = None
    vwap: Optional[float] = None
    ema9: Optional[float] = None
    ema21: Optional[float] = None
    ema50: Optional[float] = None
    rsi: Optional[float] = None
    adx: Optional[float] = None
    htf_close: Optional[float] = None
    htf_ema50: Optional[float] = None


@dataclass
class SetupTriggers:
    """External setup signals fed in from the v1 detector."""
    orb_long: bool = False
    orb_short: bool = False
    orb_pending: bool = False
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    or_set: bool = False

    ema_long: bool = False
    ema_short: bool = False
    ema_trend_bull: bool = False
    ema_trend_bear: bool = False
    ema_in_zone: bool = False

    sweep_long: bool = False
    sweep_short: bool = False
    reclaim_up_armed: bool = False
    reclaim_dn_armed: bool = False
    bos_bull_active: bool = False
    bos_bear_active: bool = False

    orb_score: int = 0    # 0-5
    ema_score: int = 0    # 0-6
    sweep_score: int = 0  # 0-5


@dataclass
class FirmDecision:
    """Output of one bar's evaluation."""
    fire_long: bool
    fire_short: bool
    setup_name: str
    pm_final: float
    quant_total: float
    voice_agree: int
    red_team: float
    red_team_weighted: float
    regime: str
    voices: dict
    direction: int  # -1, 0, 1
    blocked_reason: str = ""  # "" if fired, else why not


@dataclass
class FirmConfig:
    pm_threshold: float = 40.0  # Calibrated empirically; spec value 75 was unreachable with weighted-avg math
    redteam_weight: float = 1.0
    require_setup: bool = True

    # Voice weight endpoints (Risk-On / Risk-Off interpolated by regime)
    w_orb_riskon: float = 1.5
    w_orb_riskoff: float = 0.5
    w_ema_riskon: float = 1.2
    w_ema_riskoff: float = 0.8
    w_sweep_riskon: float = 0.8
    w_sweep_riskoff: float = 1.5

    # New voices
    w_v4_riskon: float = 0.7
    w_v4_riskoff: float = 1.3
    w_v5_riskon: float = 1.4
    w_v5_riskoff: float = 0.6
    w_v6_neutral: float = 1.0
    w_v7_riskon: float = 0.8
    w_v7_riskoff: float = 1.2

    # Intermarket voices (V8-V11)
    w_v8_vix: float = 1.5
    w_v9_es: float = 1.3
    w_v10_dxy: float = 0.6
    w_v11_tick: float = 0.8

    # Edge stack voices (V12-V15) — advisory weights, tuned empirically
    w_v12_delta: float = 0.6    # Cumulative Delta proxy (advisory)
    w_v13_killzone: float = 0.7  # ICT Killzone (boost signals, don't kill them)
    w_v14_premdisc: float = 0.4  # Premium/Discount (light advisory)
    w_v15_fvg: float = 0.5       # Fair Value Gap (rare pattern, low weight)

    # Premium/Discount range lookback for V14
    premdisc_lookback: int = 50  # bars to compute dealing range

    # Daily P&L circuit breaker
    daily_loss_half_size: float = -1.0   # After -1R on day, half size
    daily_loss_pause: float = -2.0       # After -2R on day, no new trades
    consecutive_losing_days_pause: int = 2  # 2 losing days in a row = pause

    # Regime thresholds
    regime_adx_riskon: float = 22.0
    regime_adx_riskoff: float = 15.0
    regime_atr_ratio_max: float = 2.0
    regime_vol_z_crisis: float = 3.0


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b not in (0, 0.0) else default


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


# ─────────────────────────────────────────────────────────────────────────────
# REGIME DETECTOR
# ─────────────────────────────────────────────────────────────────────────────
def detect_regime(adx: float, atr: float, atr_ma20: float, vol_z: float,
                  cfg: FirmConfig = FirmConfig()) -> str:
    atr_ratio = _safe_div(atr, atr_ma20, 1.0)
    if atr_ratio > cfg.regime_atr_ratio_max or vol_z > cfg.regime_vol_z_crisis:
        return "CRISIS"
    if adx >= cfg.regime_adx_riskon and 0.9 <= atr_ratio <= 1.6:
        return "RISK-ON"
    if adx <= cfg.regime_adx_riskoff and atr_ratio < 0.9:
        return "RISK-OFF"
    return "NEUTRAL"


def regime_blend(regime: str) -> float:
    """0.0 = Risk-Off, 1.0 = Risk-On, 0.5 = Neutral/Crisis."""
    return {"RISK-ON": 1.0, "RISK-OFF": 0.0, "NEUTRAL": 0.5, "CRISIS": 0.5}[regime]


def red_weight_for_regime(regime: str) -> float:
    return {"CRISIS": 1.5, "RISK-OFF": 1.2, "RISK-ON": 0.8, "NEUTRAL": 1.0}[regime]


def compute_voice_weights(regime: str, cfg: FirmConfig) -> dict:
    b = regime_blend(regime)
    return {
        "v1": cfg.w_orb_riskoff   + (cfg.w_orb_riskon   - cfg.w_orb_riskoff)   * b,
        "v2": cfg.w_ema_riskoff   + (cfg.w_ema_riskon   - cfg.w_ema_riskoff)   * b,
        "v3": cfg.w_sweep_riskoff + (cfg.w_sweep_riskon - cfg.w_sweep_riskoff) * b,
        "v4": cfg.w_v4_riskoff    + (cfg.w_v4_riskon    - cfg.w_v4_riskoff)    * b,
        "v5": cfg.w_v5_riskoff    + (cfg.w_v5_riskon    - cfg.w_v5_riskoff)    * b,
        "v6": cfg.w_v6_neutral,
        "v7": cfg.w_v7_riskoff    + (cfg.w_v7_riskon    - cfg.w_v7_riskoff)    * b,
        "v8": cfg.w_v8_vix,
        "v9": cfg.w_v9_es,
        "v10": cfg.w_v10_dxy,
        "v11": cfg.w_v11_tick,
        "v12": cfg.w_v12_delta,
        "v13": cfg.w_v13_killzone,
        "v14": cfg.w_v14_premdisc,
        "v15": cfg.w_v15_fvg,
    }


# ─────────────────────────────────────────────────────────────────────────────
# THE 7 VOICES
# ─────────────────────────────────────────────────────────────────────────────
def voice_orb(bar: Bar, st: SetupTriggers) -> float:
    if not st.or_set:
        return 0.0
    if st.orb_long:
        # When ORB fires + setup score 4-5, voice should max near +100
        return 90.0 + st.orb_score * 2.0  # 90-100
    if st.orb_short:
        return -(90.0 + st.orb_score * 2.0)
    if st.orb_pending and st.or_high is not None and bar.atr:
        if bar.close > st.or_high - bar.atr * 0.2:
            return 35.0
        if st.or_low is not None and bar.close < st.or_low + bar.atr * 0.2:
            return -35.0
    return 0.0


def voice_ema(bar: Bar, st: SetupTriggers) -> float:
    if st.ema_long:
        # EMA score 4-6 → voice 80-100
        return 80.0 + st.ema_score * 3.0
    if st.ema_short:
        return -(80.0 + st.ema_score * 3.0)
    if st.ema_trend_bull and st.ema_in_zone:
        return 35.0
    if st.ema_trend_bear and st.ema_in_zone:
        return -35.0
    if st.ema_trend_bull:
        return 15.0
    if st.ema_trend_bear:
        return -15.0
    return 0.0


def voice_sweep(bar: Bar, st: SetupTriggers) -> float:
    if st.sweep_long:
        return 90.0 + st.sweep_score * 2.0
    if st.sweep_short:
        return -(90.0 + st.sweep_score * 2.0)
    if st.reclaim_up_armed:
        return 45.0
    if st.reclaim_dn_armed:
        return -45.0
    if st.bos_bull_active:
        return 20.0
    if st.bos_bear_active:
        return -20.0
    return 0.0


def voice_vwap_mr(bar: Bar) -> float:
    if bar.vwap is None or bar.atr is None or bar.atr <= 0 or bar.rsi is None:
        return 0.0
    dist = (bar.close - bar.vwap) / bar.atr
    if dist > 1.5 and bar.close < bar.open and bar.rsi > 65:
        return -60.0 - min(dist * 5, 40)
    if dist < -1.5 and bar.close > bar.open and bar.rsi < 35:
        return 60.0 + min(abs(dist) * 5, 40)
    if dist > 0.5:
        return 15.0
    if dist < -0.5:
        return -15.0
    return 0.0


def voice_momentum(bar: Bar, prev_adx_3: float, vol_z: float) -> float:
    if bar.atr is None or bar.adx is None:
        return 0.0
    body = abs(bar.close - bar.open)
    rng = bar.high - bar.low
    body_ratio = _safe_div(body, rng)
    big_bar = body >= bar.atr * 0.7 and body_ratio >= 0.6
    vol_burst = vol_z >= 1.5
    adx_rising = bar.adx > prev_adx_3
    if big_bar and vol_burst and adx_rising:
        bonus = 15.0 if vol_z >= 2.5 else 0.0
        return 75.0 + bonus if bar.close > bar.open else -(75.0 + bonus)
    if big_bar and vol_burst:
        return 40.0 if bar.close > bar.open else -40.0
    if big_bar:
        return 20.0 if bar.close > bar.open else -20.0
    return 0.0


def voice_htf(bar: Bar) -> float:
    if bar.htf_close is None or bar.htf_ema50 is None or bar.atr is None or bar.atr <= 0:
        return 0.0
    htf_dist = abs(bar.htf_close - bar.htf_ema50) / bar.atr
    if bar.htf_close > bar.htf_ema50:
        return 30.0 + min(htf_dist * 15, 50)
    if bar.htf_close < bar.htf_ema50:
        return -(30.0 + min(htf_dist * 15, 50))
    return 0.0


def voice_liqvac(bar: Bar, range_avg_20: float, vol_z_prev_1: float, vol_z_prev_2: float,
                 highest_5_prev: float, lowest_5_prev: float) -> float:
    rng = bar.high - bar.low
    range_expand = range_avg_20 > 0 and rng / range_avg_20 > 1.4
    thin_vol_prior = vol_z_prev_1 < -0.5 and vol_z_prev_2 < -0.5
    if range_expand and thin_vol_prior:
        if bar.close > bar.open and bar.close > highest_5_prev:
            return 65.0
        if bar.close < bar.open and bar.close < lowest_5_prev:
            return -65.0
    if range_expand:
        return 25.0 if bar.close > bar.open else -25.0
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# V8: VIX Spike Voice
# Risk-off signal when VIX spikes; risk-on when VIX is low and dropping
# ─────────────────────────────────────────────────────────────────────────────
def voice_vix(bar: Bar, vix_ma: Optional[float] = None) -> float:
    """Score based on VIX level + change.
    - VIX > 25 = risk-off, penalize trend trades (negative for any direction)
    - VIX < 15 = complacency, breakout-friendly
    - VIX rising sharply = panic, strong risk-off
    """
    vix = getattr(bar, 'vix_close', None)
    vix_open = getattr(bar, 'vix_open', None)
    if vix is None:
        return 0.0
    vix_change_pct = ((vix - vix_open) / vix_open * 100) if vix_open and vix_open > 0 else 0.0

    # VIX spiking up (>5% intra-bar) = sell signal
    if vix_change_pct > 5:
        return -70.0  # Strong short bias (NQ falls when VIX spikes)
    if vix_change_pct < -5:
        return 50.0   # VIX collapsing = relief rally signal

    # Absolute level scoring
    if vix > 30:
        return -50.0  # crisis-level fear, broad de-risking
    if vix > 25:
        return -30.0  # elevated fear
    if vix > 20:
        return -15.0  # caution
    if vix < 13:
        return 25.0   # complacency = continued grind up
    if vix < 16:
        return 10.0   # low fear environment
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# V9: ES/NQ Correlation Voice
# When NQ moves against ES, the move is unsustainable (broad market disagrees)
# ─────────────────────────────────────────────────────────────────────────────
def voice_es_corr(bar: Bar) -> float:
    """Compare NQ direction vs ES direction this bar.
    - Both moving same direction = confluence (boost)
    - NQ against ES = divergence, mean-reversion likely
    """
    es_close = getattr(bar, 'es_close', None)
    es_open = getattr(bar, 'es_open', None)
    if es_close is None or es_open is None:
        return 0.0
    nq_change = bar.close - bar.open
    es_change = es_close - es_open
    if es_open == 0 or bar.open == 0:
        return 0.0
    nq_pct = nq_change / bar.open * 100
    es_pct = es_change / es_open * 100

    # Same direction with similar magnitude = strong confluence
    if nq_pct > 0.1 and es_pct > 0.1:
        return 40.0 + min(abs(nq_pct + es_pct) * 5, 30)  # 40-70
    if nq_pct < -0.1 and es_pct < -0.1:
        return -(40.0 + min(abs(nq_pct + es_pct) * 5, 30))

    # Divergence: NQ up, ES down = unsustainable long
    if nq_pct > 0.1 and es_pct < -0.1:
        return -50.0  # Bearish for NQ (mean revert)
    if nq_pct < -0.1 and es_pct > 0.1:
        return 50.0   # Bullish for NQ

    # Small moves: light confluence/divergence
    if abs(nq_pct) < 0.05 and abs(es_pct) < 0.05:
        return 0.0
    if (nq_pct > 0) == (es_pct > 0):
        return 15.0 if nq_pct > 0 else -15.0
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# V10: DXY Risk Currents
# Strong dollar = risk-off for equities; weak dollar = risk-on
# ─────────────────────────────────────────────────────────────────────────────
def voice_dxy(bar: Bar) -> float:
    """DXY change as inverse risk indicator for NQ."""
    dxy = getattr(bar, 'dxy_close', None)
    dxy_open = getattr(bar, 'dxy_open', None)
    if dxy is None or dxy_open is None or dxy_open == 0:
        return 0.0
    dxy_pct = (dxy - dxy_open) / dxy_open * 100

    # Strong DXY move = inverse signal for NQ
    if dxy_pct > 0.3:
        return -25.0  # Dollar surge = risk-off
    if dxy_pct < -0.3:
        return 25.0   # Dollar drop = risk-on
    if dxy_pct > 0.1:
        return -10.0
    if dxy_pct < -0.1:
        return 10.0
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# V11: TICK Breadth Voice
# NYSE TICK = (advancing - declining) issues. Extremes signal exhaustion.
# ─────────────────────────────────────────────────────────────────────────────
def voice_tick(bar: Bar) -> float:
    """TICK extreme readings as breadth/exhaustion signal."""
    tick = getattr(bar, 'tick_close', None)
    if tick is None:
        return 0.0
    # TICK > +1000 or < -1000 = extreme; reversal likely
    if tick > 1000:
        return -30.0  # Buy climax = short-term top
    if tick < -1000:
        return 30.0   # Sell climax = bounce
    if tick > 600:
        return -10.0  # Strong but not extreme bullish
    if tick < -600:
        return 10.0
    if tick > 200:
        return 15.0   # Healthy bullish breadth
    if tick < -200:
        return -15.0
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# V12: Cumulative Delta Proxy — close position + body strength = order flow
# ─────────────────────────────────────────────────────────────────────────────
def voice_delta(bar: Bar) -> float:
    rng = bar.high - bar.low
    if rng <= 0:
        return 0.0
    close_pos = (bar.close - bar.low) / rng
    is_green = bar.close > bar.open
    body_pct = abs(bar.close - bar.open) / rng if rng > 0 else 0
    if is_green and close_pos > 0.7 and body_pct > 0.5:
        return 50.0 + (close_pos - 0.7) * 100  # 50-80
    if not is_green and close_pos < 0.3 and body_pct > 0.5:
        return -(50.0 + (0.3 - close_pos) * 100)
    if is_green and close_pos > 0.5:
        return 25.0
    if not is_green and close_pos < 0.5:
        return -25.0
    if is_green and close_pos < 0.3:
        return -20.0  # Buyers absorbed = exhaustion
    if not is_green and close_pos > 0.7:
        return 20.0
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# V13: ICT Killzone Filter — boost in NY AM/PM, penalize lunch & overnight
# ─────────────────────────────────────────────────────────────────────────────
def voice_killzone(bar: Bar) -> float:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    bar_dt = datetime.fromtimestamp(bar.time, tz=timezone.utc)
    et = bar_dt.astimezone(ET)
    if et.weekday() >= 5:
        return 0.0
    m = et.hour * 60 + et.minute
    if 9*60+30 <= m < 11*60+30:    # NY AM Killzone (incl London close overlap)
        return 35.0
    if 13*60+30 <= m < 15*60:       # NY PM Killzone
        return 30.0
    if 11*60+30 <= m < 13*60+30:   # Lunch chop
        return -25.0
    if 15*60 <= m < 16*60:          # MOC hour
        return 15.0
    return -30.0                    # Overnight / pre-RTH


# ─────────────────────────────────────────────────────────────────────────────
# V14: Premium/Discount filter (SMC) — long discount, short premium
# ─────────────────────────────────────────────────────────────────────────────
def voice_premdisc(bar: Bar, range_high: Optional[float], range_low: Optional[float],
                   direction_hint: int = 0) -> float:
    if range_high is None or range_low is None or range_high <= range_low:
        return 0.0
    span = range_high - range_low
    pos = (bar.close - range_low) / span  # 0=discount, 1=premium
    if direction_hint > 0:
        if pos < 0.3: return 30.0
        if pos < 0.5: return 15.0
        if pos < 0.7: return -10.0
        return -25.0
    if direction_hint < 0:
        if pos > 0.7: return 30.0
        if pos > 0.5: return 15.0
        if pos > 0.3: return -10.0
        return -25.0
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# V15: Fair Value Gap (SMC) — 3-bar imbalance pattern
# ─────────────────────────────────────────────────────────────────────────────
def voice_fvg(bar: Bar, p1_high: Optional[float] = None, p1_low: Optional[float] = None,
              p2_high: Optional[float] = None, p2_low: Optional[float] = None) -> float:
    """Score from 3-bar FVG pattern. Pass current + 2 prior bar highs/lows."""
    if p2_high is None or p2_low is None:
        return 0.0
    # Bullish FVG: p2.high < current.low (gap up between bars)
    if p2_high < bar.low and bar.close > bar.open:
        gap = bar.low - p2_high
        if bar.atr and gap > bar.atr * 0.2:
            return 60.0
        return 35.0
    # Bearish FVG: p2.low > current.high
    if p2_low > bar.high and bar.close < bar.open:
        gap = p2_low - bar.high
        if bar.atr and gap > bar.atr * 0.2:
            return -60.0
        return -35.0
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# RED TEAM
# ─────────────────────────────────────────────────────────────────────────────
def red_team(is_long: bool, bar: Bar, regime: str, recent_losses: int,
             prev_day_high: Optional[float], prev_day_low: Optional[float],
             atr_ratio: float) -> float:
    score = 0.0

    # 1. Counter-HTF
    if bar.htf_close is not None and bar.htf_ema50 is not None:
        htf_bull = bar.htf_close > bar.htf_ema50
        htf_bear = bar.htf_close < bar.htf_ema50
        if (is_long and htf_bear) or (not is_long and htf_bull):
            score += 25.0

    # 2. RSI extreme (chasing)
    if bar.rsi is not None:
        if (is_long and bar.rsi > 75) or (not is_long and bar.rsi < 25):
            score += 15.0

    # 3. Crisis regime
    if regime == "CRISIS":
        score += 30.0

    # 4. Risk-Off + low ADX (don't trade trend in chop)
    if regime == "RISK-OFF" and bar.adx is not None and bar.adx < 15:
        score += 15.0

    # 5. Key level proximity (resistance for longs / support for shorts)
    if bar.atr is not None and bar.atr > 0:
        if is_long and prev_day_high is not None and bar.close < prev_day_high:
            if (prev_day_high - bar.close) < bar.atr * 0.5:
                score += 10.0
        if not is_long and prev_day_low is not None and bar.close > prev_day_low:
            if (bar.close - prev_day_low) < bar.atr * 0.5:
                score += 10.0

    # 6. Volatility anomaly
    if atr_ratio > 1.8:
        score += 10.0

    # 7. Wrong side of VWAP for momentum
    if bar.vwap is not None and bar.rsi is not None:
        if (is_long and bar.close < bar.vwap and bar.rsi < 50) or \
           (not is_long and bar.close > bar.vwap and bar.rsi > 50):
            score += 10.0

    # Cluster penalty (recent losses)
    cluster = 15.0 if recent_losses >= 2 else 5.0 if recent_losses >= 1 else 0.0
    score += cluster

    return min(score, 100.0)


# ─────────────────────────────────────────────────────────────────────────────
# PM WEIGHTED VOTING — Final Decision
# ─────────────────────────────────────────────────────────────────────────────
def evaluate(bar: Bar, st: SetupTriggers, regime: str,
             atr_ma20: float, vol_z: float, prev_adx_3: float,
             range_avg_20: float, vol_z_prev_1: float, vol_z_prev_2: float,
             highest_5_prev: float, lowest_5_prev: float,
             recent_losses: int,
             prev_day_high: Optional[float], prev_day_low: Optional[float],
             cfg: FirmConfig = FirmConfig()) -> FirmDecision:

    # Compute voices
    v1 = voice_orb(bar, st)
    v2 = voice_ema(bar, st)
    v3 = voice_sweep(bar, st)
    v4 = voice_vwap_mr(bar)
    v5 = voice_momentum(bar, prev_adx_3, vol_z)
    v6 = voice_htf(bar)
    v7 = voice_liqvac(bar, range_avg_20, vol_z_prev_1, vol_z_prev_2,
                      highest_5_prev, lowest_5_prev)
    # Intermarket voices (return 0 when sibling data missing)
    v8 = voice_vix(bar)
    v9 = voice_es_corr(bar)
    v10 = voice_dxy(bar)
    v11 = voice_tick(bar)

    # Edge stack voices (V12-V15)
    v12 = voice_delta(bar)
    v13 = voice_killzone(bar)
    # V14 needs range high/low and direction hint
    range_hi = getattr(bar, 'range_high_50', None)
    range_lo = getattr(bar, 'range_low_50', None)
    # Direction hint comes from setup voices
    dir_hint = 1 if (st.orb_long or st.ema_long or st.sweep_long) else \
               -1 if (st.orb_short or st.ema_short or st.sweep_short) else 0
    v14 = voice_premdisc(bar, range_hi, range_lo, dir_hint)
    # V15 needs prior 2 bars
    p2_h = getattr(bar, 'p2_high', None)
    p2_l = getattr(bar, 'p2_low', None)
    v15 = voice_fvg(bar, p2_high=p2_h, p2_low=p2_l)

    voices = {"v1": v1, "v2": v2, "v3": v3, "v4": v4,
              "v5": v5, "v6": v6, "v7": v7,
              "v8": v8, "v9": v9, "v10": v10, "v11": v11,
              "v12": v12, "v13": v13, "v14": v14, "v15": v15}

    weights = compute_voice_weights(regime, cfg)
    # Normalize: only count weight from voices with non-zero data
    active_keys = []
    for k, v in voices.items():
        if k in ("v8", "v9", "v10", "v11"):
            data_present = (
                (k == "v8" and getattr(bar, 'vix_close', None) is not None) or
                (k == "v9" and getattr(bar, 'es_close', None) is not None) or
                (k == "v10" and getattr(bar, 'dxy_close', None) is not None) or
                (k == "v11" and getattr(bar, 'tick_close', None) is not None)
            )
            if data_present:
                active_keys.append(k)
        elif k == "v14":
            # Premium/discount only active when range is computed and there's a direction hint
            if range_hi is not None and range_lo is not None and dir_hint != 0:
                active_keys.append(k)
        elif k == "v15":
            # FVG only active when prior 2 bars data present
            if p2_h is not None and p2_l is not None:
                active_keys.append(k)
        else:
            active_keys.append(k)

    if active_keys:
        active_w = sum(weights[k] for k in active_keys)
        quant_raw = sum(voices[k] * weights[k] for k in active_keys)
        quant_total = _safe_div(quant_raw, active_w)
    else:
        quant_total = 0.0

    direction = _sign(quant_total)
    voice_agree = sum(1 for k in active_keys if _sign(voices[k]) == direction and direction != 0)

    # Red Team for both directions, pick by quant direction
    is_long = direction > 0
    atr_ratio = _safe_div(bar.atr or 0, atr_ma20, 1.0)
    red = red_team(is_long, bar, regime, recent_losses,
                   prev_day_high, prev_day_low, atr_ratio)
    red_w = red_weight_for_regime(regime) * cfg.redteam_weight
    red_weighted = red * red_w

    # PM final
    pm_final = abs(quant_total) - red_weighted
    pm_pass = pm_final >= cfg.pm_threshold

    # Setup determination
    setup_name = ""
    if st.sweep_long or st.sweep_short:
        setup_name = "SWEEP"
    elif st.orb_long or st.orb_short:
        setup_name = "ORB"
    elif st.ema_long or st.ema_short:
        setup_name = "EMA PB"

    setup_long = st.orb_long or st.ema_long or st.sweep_long
    setup_short = st.orb_short or st.ema_short or st.sweep_short

    # Mode resolution
    if cfg.require_setup:
        fire_long = setup_long and pm_pass and direction > 0
        fire_short = setup_short and pm_pass and direction < 0
    else:
        fire_long = pm_pass and direction > 0
        fire_short = pm_pass and direction < 0
        if not setup_name:
            setup_name = "FIRM"

    # Crisis lockdown
    blocked = ""
    if regime == "CRISIS":
        if fire_long or fire_short:
            blocked = "crisis_lockdown"
        fire_long = False
        fire_short = False
    elif not pm_pass and direction != 0:
        blocked = f"pm_below_threshold ({pm_final:.1f} < {cfg.pm_threshold})"
    elif cfg.require_setup and not (setup_long or setup_short) and direction != 0:
        blocked = "no_setup_trigger"

    return FirmDecision(
        fire_long=fire_long,
        fire_short=fire_short,
        setup_name=setup_name if (fire_long or fire_short) else "",
        pm_final=round(pm_final, 2),
        quant_total=round(quant_total, 2),
        voice_agree=voice_agree,
        red_team=round(red, 2),
        red_team_weighted=round(red_weighted, 2),
        regime=regime,
        voices={k: round(v, 1) for k, v in voices.items()},
        direction=direction,
        blocked_reason=blocked,
    )


__all__ = [
    "Bar", "SetupTriggers", "FirmConfig", "FirmDecision",
    "detect_regime", "evaluate",
    "voice_orb", "voice_ema", "voice_sweep", "voice_vwap_mr",
    "voice_momentum", "voice_htf", "voice_liqvac",
    "voice_vix", "voice_es_corr", "voice_dxy", "voice_tick",
    "red_team", "compute_voice_weights", "red_weight_for_regime",
]
