"""
Apex v2 Backtest Engine — FINE-TUNED
====================================
Full port of v1 detector with every backtested filter preserved:
  - ORB: vol multiplier, OCO, timeout 15 bars, retest mode option
  - EMA Pullback: Skip Thursday, score >= 4, Power Hours, OR-bias alignment,
    wave freshness (PB#0 only), 50% size, ATR or Swing SL
  - Sweep: BOS+OB gating, 40-bar BOS validity, reclaim windows, sweep_sl_ticks

Trade simulation:
  - Per-setup R-multiples (ORB 1.5/3, EMA 1/2, Sweep 1/2)
  - 9 EMA runner trail
  - Partial exits (50% TP1, BE stop, trail rest) when --use-partials
  - EMA size 50%

Usage:
    python backtest.py mnq_5m.csv
    python backtest.py mnq_5m.csv --pm 35 -v
"""

import argparse
import csv
from dataclasses import dataclass, field
from typing import List, Optional, Deque
from collections import deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from firm_engine import (
    Bar, SetupTriggers, FirmConfig, FirmDecision,
    detect_regime, evaluate,
)
from indicator_state import IndicatorState

ET = ZoneInfo("America/New_York")
TICK = 0.25


@dataclass
class V1DetectorConfig:
    # ORB
    orb_tick_buf: int = 4
    orb_vol_mult: float = 1.5
    orb_sl_mode: str = "Structure"
    orb_sl_ticks: int = 2
    orb_require_retest: bool = False
    orb_timeout: int = 20  # Tuned: 15 was too short on 5m data, expired too many trades
    use_oco: bool = True
    # EMA Pullback
    ema_fast_len: int = 9
    ema_slow_len: int = 21
    pb_zone_atr: float = 0.3
    ema_sl_mode: str = "ATR"
    ema_sl_atr: float = 1.5
    ema_tod_filter: str = "Power Hours"
    # DOW filter default changed from "Skip Thursday" → "All Days" in 2026-04
    # to reconcile with V3 confluence_scorer (3yr data: Thu is strongest day,
    # weight=4.0, +4.10R PF 2.03). V2's original "Skip Thursday" was tuned on
    # a narrower window and contradicted the production scoring engine.
    # Override via CLI `--ema-dow "Skip Thursday"` if backtesting legacy config.
    # See BASEMENT_THEORY_AUDIT.md Fix #2.
    ema_dow_filter: str = "All Days"
    ema_orb_align: bool = True
    ema_min_score: int = 4
    ema_adx_floor: int = 0
    max_pb: int = 1
    ema_size_pct: int = 50
    # Sweep — LOOSENED based on real data (only 1 trigger in 73d at strict params)
    swing_lb: int = 15
    sweep_wick_min: float = 0.4   # was 0.5 — slight loosening
    sweep_depth_atr: float = 0.25  # was 0.3 — slight loosening
    bos_window: int = 7  # was 5 — give reclaim more time
    sweep_entry_win: int = 5
    sweep_sl_ticks: int = 4
    sweep_bos_valid: int = 40
    # Targets
    orb_tp1_r: float = 1.5
    orb_tp2_r: float = 3.0
    ema_tp1_r: float = 1.0
    ema_tp2_r: float = 1.5  # Tuned: EMA PB MFE p90 = 1.3R, so 1.5 is the realistic stretch target
    sweep_tp1_r: float = 1.0
    sweep_tp2_r: float = 2.0
    use_runner: bool = True
    # PARTIALS ENABLED BY DEFAULT (real-data walk-forward, 2026-04-16):
    # fibonacci + partials + pullback entry = 50 trades / 78% WR / +14.31R / PF 4.18 / MDD 1.0R
    # over 7 years of MNQ 5m data (490,103 bars 2019-05 → 2026-04).
    # Disabling partials collapses edge; verified across multiple sweeps.
    use_partials: bool = True
    # Risk
    cooldown: int = 12
    min_score: int = 3

    # ─── EXECUTION INTELLIGENCE (data-driven from MAE/MFE analysis) ───
    # Entry refinement
    # PULLBACK DEFAULT (real-data calibration 2026-04-16): market entry gave
    # identical signal count but worse avg-fill; pullback limit at 0.3 ATR
    # produced the winning config above. Override via --entry-mode market.
    entry_mode: str = "pullback"  # "market" or "pullback" — pullback uses limit at signal_close - pullback_atr*ATR
    pullback_atr: float = 0.3   # Limit entry: signal_close ± 0.3 * ATR (long minus, short plus)
    pullback_max_wait: int = 3  # Cancel limit after N bars if not filled, fall back to market

    # Setup-specific timeouts (data-driven: MFE@bar p90 × 1.5)
    ema_timeout: int = 12       # EMA PB peaks bar 5, p90 bar 7 → 12 bars
    sweep_timeout: int = 20     # Sweep undertested in our data, default conservative

    # MFE-aware trailing
    use_mfe_trail: bool = True
    trail_arm_R: float = 0.6    # Once trade reaches +0.6R MFE...
    trail_lock_R: float = 0.3   # ...move SL to entry + 0.3R (lock 0.3R profit)

    # Setup-specific TP2 adjustments (informed by winner MFE p90)
    # EMA PB winners peaked at MFE p90 = 1.3R, so 1.5R TP2 is appropriate (was 2.0)
    # Set ema_tp2_r conservatively below MFE p50

    # ─── EXIT MODE (data-driven exit logic) ───
    # "r_multiple": fixed TP1/TP2 at R-multiples (legacy default)
    # "alligator":  exit on Lips cross-back (best for trend-following EMA PB)
    # "fibonacci":  TP at Fib extensions of underlying swing — WINNING DEFAULT
    # "hybrid":     setup-specific best — Alligator for EMA PB, Fibonacci for ORB/Sweep
    #
    # FIBONACCI DEFAULT (real-data walk-forward, 2026-04-16):
    # Across 7yr MNQ 5m data, `fibonacci + use_partials=True + entry_mode=pullback`
    # is the only config that survives OOS. r_multiple (the old default) gave
    # 8 trades / -1.2R over the same window. Fibonacci exits on the golden
    # extension (1.272×) capture the structural targets of ORB and Sweep setups
    # while partials lock risk at tp1. This combination produces the headline:
    #     50 trades / 78.0% WR / +14.31R / PF 4.18 / Max DD 1.0R
    exit_mode: str = "fibonacci"
    alligator_exit_bars: int = 2  # Require N consecutive closes against Lips before exit
    fib_tp1_extension: float = 1.272  # First take profit at golden extension
    fib_tp2_extension: float = 1.618  # Stretch target at full Fib extension


@dataclass
class V1Detector:
    cfg: V1DetectorConfig = field(default_factory=V1DetectorConfig)
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    or_set: bool = False
    orb_long_triggered: bool = False
    orb_short_triggered: bool = False
    or_broke_up: bool = False
    or_broke_dn: bool = False
    current_day: Optional[int] = None
    in_orb_window_prev: bool = False
    in_session_prev: bool = False
    last_orb_bar: int = -999
    last_ema_bar: int = -999
    last_sweep_bar: int = -999
    pb_count_bull: int = 0
    pb_count_bear: int = 0
    was_bull: bool = False
    was_bear: bool = False
    was_in_zone: bool = False
    last_swing_hi: Optional[float] = None
    last_swing_lo: Optional[float] = None
    bos_bull_active: bool = False
    bos_bear_active: bool = False
    bos_bull_bar: int = -999
    bos_bear_bar: int = -999
    ob_demand_hi: Optional[float] = None
    ob_demand_lo: Optional[float] = None
    ob_supply_hi: Optional[float] = None
    ob_supply_lo: Optional[float] = None
    reclaim_up_armed: bool = False
    reclaim_dn_armed: bool = False
    reclaim_up_bar: int = -999
    reclaim_dn_bar: int = -999
    swept_lo_px: Optional[float] = None
    swept_hi_px: Optional[float] = None
    sweep_lo_bar: int = -999
    sweep_hi_bar: int = -999
    high_history: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    low_history: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    close_history: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    open_history: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    rth_vol_sum: float = 0.0
    rth_vol_count: int = 0
    today_open_px: Optional[float] = None

    def _new_day(self):
        self.or_high = None; self.or_low = None; self.or_set = False
        self.orb_long_triggered = False; self.orb_short_triggered = False
        self.or_broke_up = False; self.or_broke_dn = False
        self.bos_bull_active = False; self.bos_bear_active = False
        self.rth_vol_sum = 0.0; self.rth_vol_count = 0
        self.today_open_px = None

    def _et_min(self, bar_dt):
        et = bar_dt.astimezone(ET)
        return et.hour * 60 + et.minute, et.weekday(), et.isoweekday()

    def _is_rth(self, bar_dt):
        m, wd, _ = self._et_min(bar_dt)
        return wd < 5 and 9*60+30 <= m < 16*60

    def _is_orb_window(self, bar_dt):
        m, wd, _ = self._et_min(bar_dt)
        return wd < 5 and 9*60+30 <= m < 9*60+45

    def _ema_tod_ok(self, bar_dt):
        m, _, _ = self._et_min(bar_dt)
        f = self.cfg.ema_tod_filter
        if f == "Full Session": return True
        if f == "Morning Only": return 9*60+30 <= m < 11*60+30
        if f == "Avoid Lunch": return not (11*60+30 <= m < 13*60+30)
        if f == "Power Hours":
            morning = 9*60+30 <= m < 11*60+30
            power = 14*60+30 <= m < 16*60
            return morning or power
        return True

    def _ema_dow_ok(self, bar_dt):
        _, _, dow = self._et_min(bar_dt)
        f = self.cfg.ema_dow_filter
        if f == "All Days": return True
        if f == "Skip Thursday": return dow != 4
        if f == "Skip Thu+Fri": return dow != 4 and dow != 5
        if f == "Mon-Tue Only": return dow == 1 or dow == 2
        return True

    def _orb_bias_long(self):
        if not self.cfg.ema_orb_align: return True
        if not self.or_set: return True
        return self.or_broke_up or (not self.or_broke_up and not self.or_broke_dn)

    def _orb_bias_short(self):
        if not self.cfg.ema_orb_align: return True
        if not self.or_set: return True
        return self.or_broke_dn or (not self.or_broke_up and not self.or_broke_dn)

    def _f_near(self, lv, close, atr):
        if lv is None or atr is None: return False
        return abs(close - lv) <= atr * 1.5

    def _orb_score(self, is_long, bar, vol_z, near_key):
        s = 0
        if bar.htf_close is not None and bar.htf_ema50 is not None:
            if (is_long and bar.htf_close > bar.htf_ema50) or (not is_long and bar.htf_close < bar.htf_ema50):
                s += 1
        if vol_z >= 1.5: s += 1
        if bar.adx is not None and bar.adx >= 20: s += 1
        if bar.vwap is not None:
            if (is_long and bar.close > bar.vwap) or (not is_long and bar.close < bar.vwap):
                s += 1
        if near_key: s += 1
        return s

    def _ema_score(self, is_long, bar, has_rejection, near_key, vol_z):
        s = 1  # trend alignment guaranteed at this point
        if bar.adx is not None and bar.adx >= 22: s += 1
        if has_rejection: s += 1
        if bar.atr is not None and bar.ema21 is not None:
            if is_long and bar.low > bar.ema21 - bar.atr * 0.3: s += 1
            elif not is_long and bar.high < bar.ema21 + bar.atr * 0.3: s += 1
        if near_key: s += 1
        if bar.vwap is not None:
            if (is_long and bar.close > bar.vwap) or (not is_long and bar.close < bar.vwap):
                s += 1
        return s

    def _sweep_score(self, is_long, bar, vol_z, near_key):
        s = 0
        if vol_z >= 1.0: s += 1
        if near_key: s += 1
        if bar.adx is not None and bar.adx <= 25: s += 1
        if bar.htf_close is not None and bar.htf_ema50 is not None:
            if (is_long and bar.htf_close > bar.htf_ema50) or (not is_long and bar.htf_close < bar.htf_ema50):
                s += 1
        if bar.vwap is not None:
            if (is_long and bar.close > bar.vwap) or (not is_long and bar.close < bar.vwap):
                s += 1
        return s

    def detect(self, bar_idx, bar, state):
        # Attach range and prior-bar metadata for V14 (premdisc) and V15 (FVG)
        if len(self.high_history) >= 50:
            bar.range_high_50 = max(list(self.high_history)[-50:])
            bar.range_low_50 = min(list(self.low_history)[-50:])
        else:
            bar.range_high_50 = max(self.high_history) if self.high_history else None
            bar.range_low_50 = min(self.low_history) if self.low_history else None
        if len(self.high_history) >= 2:
            bar.p2_high = self.high_history[-2]
            bar.p2_low = self.low_history[-2]
        else:
            bar.p2_high = None
            bar.p2_low = None

        bar_dt = datetime.fromtimestamp(bar.time, tz=timezone.utc)
        day = bar.time // 86400
        if self.current_day != day:
            self.current_day = day
            self._new_day()

        in_orb = self._is_orb_window(bar_dt)
        in_session = self._is_rth(bar_dt)
        orb_ended = (not in_orb) and self.in_orb_window_prev

        if in_orb:
            self.or_high = bar.high if self.or_high is None else max(self.or_high, bar.high)
            self.or_low = bar.low if self.or_low is None else min(self.or_low, bar.low)
            if self.today_open_px is None:
                self.today_open_px = bar.open
        if orb_ended and not self.or_set:
            self.or_set = True

        if in_session:
            self.rth_vol_sum += bar.volume
            self.rth_vol_count += 1
        vol_ma = (self.rth_vol_sum / self.rth_vol_count) if self.rth_vol_count > 5 else (
            sum(state._vol_history) / len(state._vol_history) if state._vol_history else bar.volume)

        vol_z = state.vol_z()
        near_key = (self._f_near(state.prev_day_high, bar.close, bar.atr or 0)
                    or self._f_near(state.prev_day_low, bar.close, bar.atr or 0)
                    or self._f_near(self.today_open_px, bar.close, bar.atr or 0))

        st = SetupTriggers()
        st.or_high = self.or_high
        st.or_low = self.or_low
        st.or_set = self.or_set

        # ORB (vol-gated, OCO-aware, score-gated)
        cd_orb = bar_idx - self.last_orb_bar >= self.cfg.cooldown
        orb_pending = (self.or_set and in_session
                       and not self.orb_long_triggered and not self.orb_short_triggered)
        st.orb_pending = orb_pending
        if orb_pending and self.or_high is not None and self.or_low is not None and bar.atr:
            buf = self.cfg.orb_tick_buf * TICK
            entry_long = self.or_high + buf
            entry_short = self.or_low - buf
            vol_ok = bar.volume >= vol_ma * self.cfg.orb_vol_mult
            if bar.close > entry_long and vol_ok and cd_orb:
                self.orb_long_triggered = True
                self.or_broke_up = True
                score = self._orb_score(True, bar, vol_z, near_key)
                if score >= self.cfg.min_score:
                    st.orb_long = True
                    st.orb_score = score
                    self.last_orb_bar = bar_idx
                    if self.cfg.use_oco:
                        self.orb_short_triggered = True
            elif bar.close < entry_short and vol_ok and cd_orb:
                self.orb_short_triggered = True
                self.or_broke_dn = True
                score = self._orb_score(False, bar, vol_z, near_key)
                if score >= self.cfg.min_score:
                    st.orb_short = True
                    st.orb_score = score
                    self.last_orb_bar = bar_idx
                    if self.cfg.use_oco:
                        self.orb_long_triggered = True

        # EMA Pullback (full v1 logic with all filters)
        if (bar.ema9 is not None and bar.ema21 is not None and bar.atr is not None
                and len(self.close_history) >= 1):
            trend_bull = bar.ema9 > bar.ema21
            trend_bear = bar.ema9 < bar.ema21
            st.ema_trend_bull = trend_bull
            st.ema_trend_bear = trend_bear

            zone_top = max(bar.ema9, bar.ema21) + bar.atr * self.cfg.pb_zone_atr
            zone_bot = min(bar.ema9, bar.ema21) - bar.atr * self.cfg.pb_zone_atr
            in_zone = bar.low <= zone_top and bar.high >= zone_bot
            st.ema_in_zone = in_zone

            # Wave freshness counter
            if trend_bull and not self.was_bull: self.pb_count_bull = 0
            if trend_bear and not self.was_bear: self.pb_count_bear = 0
            if in_zone and not self.was_in_zone:
                if trend_bull: self.pb_count_bull += 1
                if trend_bear: self.pb_count_bear += 1
            self.was_bull = trend_bull
            self.was_bear = trend_bear
            self.was_in_zone = in_zone
            pb_count = self.pb_count_bull if trend_bull else self.pb_count_bear
            wave_ok = pb_count <= (self.cfg.max_pb + 1)

            # Rejection candle
            prev_close = self.close_history[-1]
            prev_open = self.open_history[-1] if self.open_history else bar.open
            bull_engulf = bar.close > bar.open and bar.open < prev_close and bar.close > prev_open
            bear_engulf = bar.close < bar.open and bar.open > prev_close and bar.close < prev_open
            pin_bull = ((bar.open - bar.low) > (bar.high - bar.close) * 2 and (bar.high - bar.low) > bar.atr * 0.8)
            pin_bear = ((bar.high - bar.open) > (bar.close - bar.low) * 2 and (bar.high - bar.low) > bar.atr * 0.8)
            bull_reject = bull_engulf or pin_bull
            bear_reject = bear_engulf or pin_bear

            ema_in_range = abs(bar.close - bar.ema9) <= bar.atr * 1.0
            tod_ok = self._ema_tod_ok(bar_dt)
            dow_ok = self._ema_dow_ok(bar_dt)
            adx_ok = self.cfg.ema_adx_floor == 0 or (bar.adx is not None and bar.adx >= self.cfg.ema_adx_floor)
            cd_ema = bar_idx - self.last_ema_bar >= self.cfg.cooldown

            if (trend_bull and in_zone and bull_reject and ema_in_range and wave_ok
                    and tod_ok and dow_ok and adx_ok and self._orb_bias_long()
                    and in_session and cd_ema):
                if bar.close > prev_close and (bar.close - prev_close) <= bar.atr * 0.4:
                    score = self._ema_score(True, bar, bull_reject, near_key, vol_z)
                    if score >= self.cfg.ema_min_score:
                        st.ema_long = True
                        st.ema_score = score
                        self.last_ema_bar = bar_idx
            if (trend_bear and in_zone and bear_reject and ema_in_range and wave_ok
                    and tod_ok and dow_ok and adx_ok and self._orb_bias_short()
                    and in_session and cd_ema):
                if bar.close < prev_close and (prev_close - bar.close) <= bar.atr * 0.4:
                    score = self._ema_score(False, bar, bear_reject, near_key, vol_z)
                    if score >= self.cfg.ema_min_score:
                        st.ema_short = True
                        st.ema_score = score
                        self.last_ema_bar = bar_idx

        # Sweep + Reclaim (BOS-gated, OB-validated)
        if len(self.high_history) >= self.cfg.swing_lb + 5:
            recent_h = list(self.high_history)[-self.cfg.swing_lb:-3]
            recent_l = list(self.low_history)[-self.cfg.swing_lb:-3]
            if recent_h and bar.high > max(recent_h):
                self.last_swing_hi = bar.high
            if recent_l and bar.low < min(recent_l):
                self.last_swing_lo = bar.low

        if (self.last_swing_hi is not None and len(self.close_history) > 0
                and bar.close > self.last_swing_hi
                and self.close_history[-1] <= self.last_swing_hi):
            self.bos_bull_active = True
            self.bos_bull_bar = bar_idx
            for i in range(min(10, len(self.close_history))):
                idx = -1 - i
                if abs(idx) > len(self.close_history): break
                if self.close_history[idx] < self.open_history[idx]:
                    self.ob_demand_hi = self.high_history[idx]
                    self.ob_demand_lo = self.low_history[idx]
                    break
        if (self.last_swing_lo is not None and len(self.close_history) > 0
                and bar.close < self.last_swing_lo
                and self.close_history[-1] >= self.last_swing_lo):
            self.bos_bear_active = True
            self.bos_bear_bar = bar_idx
            for i in range(min(10, len(self.close_history))):
                idx = -1 - i
                if abs(idx) > len(self.close_history): break
                if self.close_history[idx] > self.open_history[idx]:
                    self.ob_supply_hi = self.high_history[idx]
                    self.ob_supply_lo = self.low_history[idx]
                    break

        if bar_idx - self.bos_bull_bar > self.cfg.sweep_bos_valid:
            self.bos_bull_active = False
        if bar_idx - self.bos_bear_bar > self.cfg.sweep_bos_valid:
            self.bos_bear_active = False
        st.bos_bull_active = self.bos_bull_active
        st.bos_bear_active = self.bos_bear_active

        if (self.bos_bull_active and self.last_swing_lo is not None and bar.atr
                and bar.low < self.last_swing_lo and bar.close > self.last_swing_lo
                and (min(bar.close, bar.open) - bar.low) > bar.atr * self.cfg.sweep_wick_min
                and (self.last_swing_lo - bar.low) >= bar.atr * self.cfg.sweep_depth_atr):
            self.reclaim_up_armed = True
            self.swept_lo_px = bar.low
            self.sweep_lo_bar = bar_idx
        if (self.bos_bear_active and self.last_swing_hi is not None and bar.atr
                and bar.high > self.last_swing_hi and bar.close < self.last_swing_hi
                and (bar.high - max(bar.close, bar.open)) > bar.atr * self.cfg.sweep_wick_min
                and (bar.high - self.last_swing_hi) >= bar.atr * self.cfg.sweep_depth_atr):
            self.reclaim_dn_armed = True
            self.swept_hi_px = bar.high
            self.sweep_hi_bar = bar_idx

        if bar_idx - self.sweep_lo_bar > self.cfg.bos_window:
            self.reclaim_up_armed = False
        if bar_idx - self.sweep_hi_bar > self.cfg.bos_window:
            self.reclaim_dn_armed = False

        min_body = 4 * TICK
        if (self.reclaim_up_armed and self.ob_demand_lo is not None
                and bar.close > self.ob_demand_lo and bar.close > bar.open
                and abs(bar.close - bar.open) >= min_body):
            self.reclaim_up_bar = bar_idx
            self.reclaim_up_armed = False
        if (self.reclaim_dn_armed and self.ob_supply_hi is not None
                and bar.close < self.ob_supply_hi and bar.close < bar.open
                and abs(bar.close - bar.open) >= min_body):
            self.reclaim_dn_bar = bar_idx
            self.reclaim_dn_armed = False
        st.reclaim_up_armed = self.reclaim_up_armed
        st.reclaim_dn_armed = self.reclaim_dn_armed

        cd_sweep = bar_idx - self.last_sweep_bar >= self.cfg.cooldown
        if (1 <= bar_idx - self.reclaim_up_bar <= self.cfg.sweep_entry_win
                and bar.close > bar.open and cd_sweep and in_session
                and len(self.close_history) > 0
                and bar.close > self.close_history[-1]
                and abs(bar.close - bar.open) >= min_body):
            score = self._sweep_score(True, bar, vol_z, near_key)
            if score >= self.cfg.min_score:
                st.sweep_long = True
                st.sweep_score = score
                self.last_sweep_bar = bar_idx
        if (1 <= bar_idx - self.reclaim_dn_bar <= self.cfg.sweep_entry_win
                and bar.close < bar.open and cd_sweep and in_session
                and len(self.close_history) > 0
                and bar.close < self.close_history[-1]
                and abs(bar.close - bar.open) >= min_body):
            score = self._sweep_score(False, bar, vol_z, near_key)
            if score >= self.cfg.min_score:
                st.sweep_short = True
                st.sweep_score = score
                self.last_sweep_bar = bar_idx

        self.high_history.append(bar.high)
        self.low_history.append(bar.low)
        self.close_history.append(bar.close)
        self.open_history.append(bar.open)
        self.in_orb_window_prev = in_orb
        self.in_session_prev = in_session
        return st


@dataclass
class PendingLimit:
    """Limit order awaiting fill (pullback entry mode)."""
    created_idx: int
    created_time: int
    side: str
    setup: str
    limit_price: float
    signal_close: float
    decision: object
    st: object
    max_wait: int
    size_mult: float
    bars_waited: int = 0


@dataclass
class Trade:
    open_idx: int
    open_time: int
    setup: str
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    sl_dist: float
    pm_final: float
    quant: float
    red: float
    voices: dict
    regime: str
    size_pct: int = 100
    close_idx: Optional[int] = None
    close_time: Optional[int] = None
    close_px: Optional[float] = None
    outcome: str = "open"
    pnl_r: float = 0.0
    tp1_filled: bool = False
    # Execution analysis fields
    mae_R: float = 0.0       # Maximum Adverse Excursion in R units
    mfe_R: float = 0.0       # Maximum Favorable Excursion in R units
    mfe_bar: int = 0         # Bars from entry when MFE peaked
    bars_to_resolution: int = 0  # Total bars trade was open
    pullback_filled_at: Optional[float] = None  # If pullback entry, actual fill price


@dataclass
class Backtester:
    cfg: FirmConfig = field(default_factory=FirmConfig)
    detector_cfg: V1DetectorConfig = field(default_factory=V1DetectorConfig)
    detector: V1Detector = field(init=False)
    state: IndicatorState = field(default_factory=IndicatorState)
    trades: List[Trade] = field(default_factory=list)
    open_trades: List[Trade] = field(default_factory=list)
    pending_limits: List[PendingLimit] = field(default_factory=list)
    decisions: List[FirmDecision] = field(default_factory=list)
    recent_losses: int = 0
    bar_count: int = 0
    # Daily circuit breaker state
    current_day: Optional[int] = None
    daily_pnl_r: float = 0.0
    daily_trade_count: int = 0
    consecutive_losing_days: int = 0
    pause_until_day: Optional[int] = None
    blocked_by_circuit_breaker: int = 0  # diagnostic counter
    # Equity curve
    equity_curve: List[tuple] = field(default_factory=list)  # (time, cumulative_R)
    # Kelly sizing (optional)
    use_kelly: bool = False
    kelly_fraction: float = 0.25  # quarter-Kelly
    kelly_min_size: float = 0.25
    kelly_max_size: float = 1.5
    # Friction modeling
    slip_per_trade_R: float = 0.0  # Set to 0.05 for realistic MNQ ($0.50 round trip + 1 tick)
    # Microstructure entry refinement (requires 1m data)
    use_micro_entry: bool = False
    bars_1m: List = field(default_factory=list)  # 1m MicroBar list
    micro_refiner: Optional[object] = None
    micro_skip_count: int = 0  # signals skipped due to no micro confirmation
    micro_r_improvements: List = field(default_factory=list)  # track R improvements

    def __post_init__(self):
        self.detector = V1Detector(cfg=self.detector_cfg)
        if self.use_micro_entry and self.micro_refiner is None:
            from microstructure import MicroEntryRefiner
            self.micro_refiner = MicroEntryRefiner()

    def _kelly_size(self) -> float:
        """Compute fractional Kelly size based on rolling 20-trade history."""
        if not self.use_kelly or len(self.trades) < 10:
            return 1.0
        recent = self.trades[-20:]
        wins = [t for t in recent if t.pnl_r > 0]
        losses = [t for t in recent if t.pnl_r < 0]
        if not wins or not losses:
            return 1.0
        win_rate = len(wins) / len(recent)
        avg_win = sum(t.pnl_r for t in wins) / len(wins)
        avg_loss = abs(sum(t.pnl_r for t in losses) / len(losses))
        if avg_loss == 0:
            return 1.0
        # Kelly fraction: f* = (W*R - L) / R where R = avg_win/avg_loss
        R = avg_win / avg_loss
        kelly = (win_rate * R - (1 - win_rate)) / R
        # Apply fractional Kelly + bounds
        size = kelly * self.kelly_fraction
        return max(self.kelly_min_size, min(self.kelly_max_size, max(0.0, size) + 0.5))

    def _circuit_breaker_check(self, bar_time: int) -> tuple:
        """Returns (can_trade, size_multiplier, reason).
        Resets state on new day."""
        day = bar_time // 86400
        if self.current_day != day:
            # New day rollover
            if self.current_day is not None:
                if self.daily_pnl_r < 0:
                    self.consecutive_losing_days += 1
                else:
                    self.consecutive_losing_days = 0
            self.current_day = day
            self.daily_pnl_r = 0.0
            self.daily_trade_count = 0

        # Pause active?
        if self.pause_until_day is not None and day < self.pause_until_day:
            return False, 0.0, "consecutive_losing_days_pause"

        # Daily loss limit
        if self.daily_pnl_r <= self.cfg.daily_loss_pause:
            return False, 0.0, f"daily_loss_pause ({self.daily_pnl_r:.1f}R)"

        # Half size after first loss threshold
        if self.daily_pnl_r <= self.cfg.daily_loss_half_size:
            return True, 0.5, "half_size_after_loss"

        # Consecutive losing days check (handled at day rollover)
        if self.consecutive_losing_days >= self.cfg.consecutive_losing_days_pause:
            self.pause_until_day = day + 3  # pause until next Mon-ish (3 days)
            return False, 0.0, "consecutive_losing_days_pause"

        return True, 1.0, ""

    def run(self, bars):
        for i, bar in enumerate(bars):
            self.state.update(bar)
            self.bar_count = i
            st = self.detector.detect(i, bar, self.state)
            atr_ma20 = self.state.atr_ma20()
            vol_z = self.state.vol_z()
            regime = detect_regime(bar.adx or 20, bar.atr or 0, atr_ma20, vol_z)
            self._process_pending_limits(bar, i)
            self._manage_open(bar, i)
            d = evaluate(
                bar=bar, st=st, regime=regime,
                atr_ma20=atr_ma20, vol_z=vol_z,
                prev_adx_3=self.state.adx_3_bars_ago(),
                range_avg_20=self.state.range_avg_20(),
                vol_z_prev_1=self.state.vol_z_at(1),
                vol_z_prev_2=self.state.vol_z_at(2),
                highest_5_prev=self.state.highest_5_prev(),
                lowest_5_prev=self.state.lowest_5_prev(),
                recent_losses=self.recent_losses,
                prev_day_high=self.state.prev_day_high,
                prev_day_low=self.state.prev_day_low,
                cfg=self.cfg,
            )
            self.decisions.append(d)
            if d.fire_long or d.fire_short:
                # Circuit breaker check
                can_trade, size_mult, cb_reason = self._circuit_breaker_check(bar.time)
                if not can_trade:
                    self.blocked_by_circuit_breaker += 1
                    continue
                self._open_trade(bar, i, d, st, size_mult)
        for t in self.open_trades:
            t.outcome = "expired_eot"
            t.close_idx = self.bar_count
            t.close_px = bars[-1].close
            self.trades.append(t)
        return self._summary()

    def _process_pending_limits(self, bar, idx):
        """Check pending limit orders: fill if reached, expire if waited too long."""
        still = []
        for p in self.pending_limits:
            p.bars_waited = idx - p.created_idx
            # Check if limit price reached this bar
            filled = False
            if p.side == "long" and bar.low <= p.limit_price:
                filled = True
                fill_px = p.limit_price
            elif p.side == "short" and bar.high >= p.limit_price:
                filled = True
                fill_px = p.limit_price
            if filled:
                self._execute_trade(bar, idx, p.decision, p.side, p.setup,
                                    fill_px, p.size_mult, pullback_filled=True)
            elif p.bars_waited >= p.max_wait:
                # Fall back to market entry at current close
                self._execute_trade(bar, idx, p.decision, p.side, p.setup,
                                    bar.close, p.size_mult, pullback_filled=False)
            else:
                still.append(p)
        self.pending_limits = still

    def _execute_trade(self, bar, idx, d, side, setup, fill_price, size_mult, pullback_filled=False, micro_sl=None):
        """Open a trade at given fill price. Computes SL/TP relative to fill, not signal close.
        If micro_sl provided, overrides the computed SL with the microstructure-refined stop."""
        # Default initialize so tp1/tp2 always defined
        tp1 = fill_price
        tp2 = fill_price
        # Determine if Fibonacci-based TPs apply for this setup
        use_fib = (self.detector_cfg.exit_mode == "fibonacci" or
                   (self.detector_cfg.exit_mode == "hybrid" and setup in ("ORB", "SWEEP")))

        if setup == "ORB":
            st_or_low = getattr(self.detector, 'or_low', None)
            st_or_high = getattr(self.detector, 'or_high', None)
            if side == "long" and st_or_low is not None:
                sl = st_or_low - (bar.atr or 0) * 0.15
            elif side == "short" and st_or_high is not None:
                sl = st_or_high + (bar.atr or 0) * 0.15
            else:
                sl = fill_price - (bar.atr or 0) * 1.5 if side == "long" else fill_price + (bar.atr or 0) * 1.5

            if (use_fib and st_or_low is not None and st_or_high is not None
                    and (st_or_high - st_or_low) > (bar.atr or 0) * 0.5):  # min meaningful range
                # Fibonacci extensions of the OR range
                or_range = st_or_high - st_or_low
                if side == "long":
                    tp1 = st_or_high + or_range * (self.detector_cfg.fib_tp1_extension - 1.0)
                    tp2 = st_or_high + or_range * (self.detector_cfg.fib_tp2_extension - 1.0)
                else:
                    tp1 = st_or_low - or_range * (self.detector_cfg.fib_tp1_extension - 1.0)
                    tp2 = st_or_low - or_range * (self.detector_cfg.fib_tp2_extension - 1.0)
                tp1_r = abs(tp1 - fill_price) / abs(fill_price - sl) if abs(fill_price - sl) > 0 else 1.5
                tp2_r = abs(tp2 - fill_price) / abs(fill_price - sl) if abs(fill_price - sl) > 0 else 3.0
            else:
                tp1_r = self.detector_cfg.orb_tp1_r
                tp2_r = self.detector_cfg.orb_tp2_r
            size_pct = 100

        elif setup == "EMA PB":
            sl_dist_calc = (bar.atr or 0) * self.detector_cfg.ema_sl_atr
            sl = fill_price - sl_dist_calc if side == "long" else fill_price + sl_dist_calc
            tp1_r = self.detector_cfg.ema_tp1_r
            tp2_r = self.detector_cfg.ema_tp2_r
            size_pct = self.detector_cfg.ema_size_pct

        elif setup == "SWEEP":
            buf = self.detector_cfg.sweep_sl_ticks * TICK
            swept_lo = self.detector.swept_lo_px
            swept_hi = self.detector.swept_hi_px
            if side == "long" and swept_lo is not None:
                sl = swept_lo - buf
            elif side == "short" and swept_hi is not None:
                sl = swept_hi + buf
            else:
                sl = fill_price - (bar.atr or 0) * 1.5 if side == "long" else fill_price + (bar.atr or 0) * 1.5

            if (use_fib and swept_lo is not None and swept_hi is not None
                    and abs(swept_hi - swept_lo) > (bar.atr or 0) * 0.5):
                # Use the smaller of (swept range, 3x ATR) to prevent absurd targets when
                # session high/low spans a wide range
                swept_range = min(abs(swept_hi - swept_lo), (bar.atr or 0) * 3.0)
                if side == "long":
                    tp1 = fill_price + swept_range * self.detector_cfg.fib_tp1_extension
                    tp2 = fill_price + swept_range * self.detector_cfg.fib_tp2_extension
                else:
                    tp1 = fill_price - swept_range * self.detector_cfg.fib_tp1_extension
                    tp2 = fill_price - swept_range * self.detector_cfg.fib_tp2_extension
                tp1_r = abs(tp1 - fill_price) / abs(fill_price - sl) if abs(fill_price - sl) > 0 else 1.0
                tp2_r = abs(tp2 - fill_price) / abs(fill_price - sl) if abs(fill_price - sl) > 0 else 2.0
            else:
                tp1_r = self.detector_cfg.sweep_tp1_r
                tp2_r = self.detector_cfg.sweep_tp2_r
            size_pct = 100
        else:
            sl = fill_price - (bar.atr or 0) * 1.5 if side == "long" else fill_price + (bar.atr or 0) * 1.5
            tp1_r, tp2_r, size_pct = 1.0, 2.0, 100

        # Apply microstructure-refined SL if provided (tighter stop = better R:R)
        if micro_sl is not None:
            sl = micro_sl

        sl_dist = abs(fill_price - sl)
        if sl_dist <= 0:
            return
        # If not using fib, build TPs from R-multiples
        if setup == "EMA PB" or not use_fib:
            if side == "long":
                tp1 = fill_price + sl_dist * tp1_r
                tp2 = fill_price + sl_dist * tp2_r
            else:
                tp1 = fill_price - sl_dist * tp1_r
                tp2 = fill_price - sl_dist * tp2_r

        t = Trade(
            open_idx=idx, open_time=bar.time, setup=setup, side=side,
            entry=fill_price, sl=sl, tp1=tp1, tp2=tp2, sl_dist=sl_dist,
            pm_final=d.pm_final, quant=d.quant_total, red=d.red_team,
            voices=d.voices, regime=d.regime, size_pct=int(size_pct * size_mult),
            pullback_filled_at=fill_price if pullback_filled else None,
        )
        self.daily_trade_count += 1
        self.open_trades.append(t)

    def _open_trade(self, bar, idx, d, st, size_mult: float = 1.0):
        side = "long" if d.fire_long else "short"
        setup = d.setup_name

        # ─── Microstructure entry refinement ───
        # If 1m data available and micro mode enabled, refine entry using
        # per-strategy 1m rules. Can reject trade entirely if no micro confirm.
        if self.use_micro_entry and self.bars_1m and self.micro_refiner is not None:
            from microstructure import get_1m_bars_in_5m_window
            # Get up to 5 1m bars starting at this 5m bar's time
            next_1m = get_1m_bars_in_5m_window(self.bars_1m, bar.time, n_bars=5)
            if next_1m:
                # Compute the would-be signal entry/SL for refinement
                signal_entry = bar.close
                # Rough SL for refinement context (actual SL computed in _execute_trade)
                if setup == "ORB":
                    signal_sl = (st.or_low - (bar.atr or 0) * 0.15) if side == "long" and st.or_low \
                                else (st.or_high + (bar.atr or 0) * 0.15) if st.or_high else bar.close
                    or_h = st.or_high if st.or_high else bar.close
                    or_l = st.or_low if st.or_low else bar.close
                    micro = self.micro_refiner.refine_orb(side, signal_entry, signal_sl,
                                                          or_h, or_l, next_1m)
                elif setup == "EMA PB":
                    signal_sl = bar.close - (bar.atr or 0) * 1.5 if side == "long" \
                                else bar.close + (bar.atr or 0) * 1.5
                    micro = self.micro_refiner.refine_ema_pullback(
                        side, signal_entry, signal_sl,
                        bar.ema9 or bar.close, bar.ema21 or bar.close,
                        bar.atr or 0, next_1m)
                elif setup == "SWEEP":
                    swept = self.detector.swept_lo_px if side == "long" else self.detector.swept_hi_px
                    if swept is None:
                        swept = bar.close
                    signal_sl = swept - 4 * TICK if side == "long" else swept + 4 * TICK
                    micro = self.micro_refiner.refine_sweep(side, signal_entry, signal_sl,
                                                             swept, next_1m)
                else:
                    micro = None

                if micro is not None:
                    if not micro.entered:
                        # No micro confirmation → skip trade
                        self.micro_skip_count += 1
                        return
                    # Track R improvement from micro entry (tighter stop = higher R)
                    self.micro_r_improvements.append(micro.refined_r_mult)
                    # Use micro-refined entry and stop
                    self._execute_trade(bar, idx, d, side, setup,
                                        micro.entry_price, size_mult,
                                        micro_sl=micro.micro_sl)
                    return

        # ─── Pullback-limit entry mode ───
        if (self.detector_cfg.entry_mode == "pullback"
                and bar.atr is not None and setup != "ORB"):
            offset = self.detector_cfg.pullback_atr * bar.atr
            limit_price = bar.close - offset if side == "long" else bar.close + offset
            pending = PendingLimit(
                created_idx=idx, created_time=bar.time,
                side=side, setup=setup, limit_price=limit_price,
                signal_close=bar.close, decision=d, st=st,
                max_wait=self.detector_cfg.pullback_max_wait,
                size_mult=size_mult,
            )
            self.pending_limits.append(pending)
            return

        # Market entry path (default, or ORB always-market)
        self._execute_trade(bar, idx, d, side, setup, bar.close, size_mult)

    def _manage_open(self, bar, idx):
        still = []
        for t in self.open_trades:
            held = idx - t.open_idx
            if held < 1:
                still.append(t)
                continue

            # MAE/MFE tracking — measure unrealized excursion in R units
            if t.sl_dist > 0:
                if t.side == "long":
                    favorable = (bar.high - t.entry) / t.sl_dist
                    adverse = (bar.low - t.entry) / t.sl_dist  # negative when below entry
                else:
                    favorable = (t.entry - bar.low) / t.sl_dist
                    adverse = (t.entry - bar.high) / t.sl_dist
                if favorable > t.mfe_R:
                    t.mfe_R = favorable
                    t.mfe_bar = held
                if adverse < t.mae_R:
                    t.mae_R = adverse

            # Setup-specific timeouts (data-driven from MFE@bar analysis)
            if t.setup == "ORB":
                timeout_bars = self.detector_cfg.orb_timeout
            elif t.setup == "EMA PB":
                timeout_bars = self.detector_cfg.ema_timeout
            elif t.setup == "SWEEP":
                timeout_bars = self.detector_cfg.sweep_timeout
            else:
                timeout_bars = 30

            # MFE-aware trail: once we've reached arm threshold, lock in some profit
            if (self.detector_cfg.use_mfe_trail and t.mfe_R >= self.detector_cfg.trail_arm_R
                    and not t.tp1_filled and t.sl_dist > 0):
                lock_price = t.entry + (t.sl_dist * self.detector_cfg.trail_lock_R) if t.side == "long" \
                             else t.entry - (t.sl_dist * self.detector_cfg.trail_lock_R)
                # Tighten only — never widen
                if t.side == "long":
                    t.sl = max(t.sl, lock_price)
                else:
                    t.sl = min(t.sl, lock_price)
            if t.side == "long":
                sl_hit = bar.low <= t.sl
                tp1_hit = bar.high >= t.tp1
                tp2_hit = bar.high >= t.tp2
            else:
                sl_hit = bar.high >= t.sl
                tp1_hit = bar.low <= t.tp1
                tp2_hit = bar.low <= t.tp2
            sf = t.size_pct / 100.0

            # ─── EXIT MODE: ALLIGATOR ───
            # Determine if alligator exit applies to this setup
            mode_active_alligator = (
                self.detector_cfg.exit_mode == "alligator" or
                (self.detector_cfg.exit_mode == "hybrid" and t.setup == "EMA PB")
            )
            if (mode_active_alligator and held >= 2
                    and getattr(bar, 'alligator_lips', None) is not None):
                if t.side == "long" and bar.close < bar.alligator_lips:
                    t.alligator_against_count = getattr(t, 'alligator_against_count', 0) + 1
                elif t.side == "short" and bar.close > bar.alligator_lips:
                    t.alligator_against_count = getattr(t, 'alligator_against_count', 0) + 1
                else:
                    t.alligator_against_count = 0
                if (t.alligator_against_count >= self.detector_cfg.alligator_exit_bars
                        and t.mfe_R > 0.3):
                    t.outcome = "alligator_exit"
                    if t.side == "long":
                        t.pnl_r = (bar.close - t.entry) / t.sl_dist * sf
                    else:
                        t.pnl_r = (t.entry - bar.close) / t.sl_dist * sf
                    t.close_idx = idx; t.close_time = bar.time; t.close_px = bar.close
                    t.bars_to_resolution = held
                    self.trades.append(t)
                    self.daily_pnl_r += t.pnl_r
                    cum_r = sum(tt.pnl_r for tt in self.trades)
                    self.equity_curve.append((bar.time, round(cum_r, 2)))
                    if t.pnl_r > 0:
                        self.recent_losses = max(0, self.recent_losses - 1)
                    continue

            if self.detector_cfg.use_partials and not t.tp1_filled and tp1_hit and not sl_hit:
                t.tp1_filled = True
                t.sl = t.entry  # BE
                still.append(t)
                continue
            if self.detector_cfg.use_partials and t.tp1_filled and self.detector_cfg.use_runner:
                if bar.ema9 is not None and bar.atr is not None:
                    trail = bar.ema9 - bar.atr * 0.15 if t.side == "long" else bar.ema9 + bar.atr * 0.15
                    t.sl = max(t.sl, trail) if t.side == "long" else min(t.sl, trail)

            closed = False
            if sl_hit:
                # Compute actual P&L from SL price - handles trailed SL at profit-lock correctly
                if t.side == "long":
                    actual_r = (t.sl - t.entry) / t.sl_dist
                else:
                    actual_r = (t.entry - t.sl) / t.sl_dist
                if t.tp1_filled:
                    t.outcome = "tp1_then_be"
                    t.pnl_r = max(actual_r, 0.5) * sf
                    self.recent_losses = max(0, self.recent_losses - 1)
                elif actual_r > 0:
                    # Trailed SL at profit lock fired - this is a WIN, not a loss
                    t.outcome = "trail_lock"
                    t.pnl_r = actual_r * sf
                    self.recent_losses = max(0, self.recent_losses - 1)
                else:
                    t.outcome = "sl"
                    t.pnl_r = actual_r * sf  # actual_r is -1.0 for original SL
                    self.recent_losses += 1
                t.close_idx = idx; t.close_time = bar.time; t.close_px = t.sl
                closed = True
            elif tp2_hit:
                # PnL from actual TP2 price (handles fib TPs at custom levels)
                if t.side == "long":
                    actual_r = (t.tp2 - t.entry) / t.sl_dist
                else:
                    actual_r = (t.entry - t.tp2) / t.sl_dist
                if self.detector_cfg.use_partials and t.tp1_filled:
                    t.outcome = "tp2_partial"
                    t.pnl_r = (actual_r * 0.5 + 0.5) * sf  # 50% at TP1 + 50% at TP2
                else:
                    t.outcome = "tp2"
                    t.pnl_r = actual_r * sf
                t.close_idx = idx; t.close_time = bar.time; t.close_px = t.tp2
                closed = True
                self.recent_losses = max(0, self.recent_losses - 1)
            elif tp1_hit and not self.detector_cfg.use_partials:
                # PnL from actual TP1 price
                if t.side == "long":
                    actual_r = (t.tp1 - t.entry) / t.sl_dist
                else:
                    actual_r = (t.entry - t.tp1) / t.sl_dist
                t.outcome = "tp1"
                t.pnl_r = actual_r * sf
                t.close_idx = idx; t.close_time = bar.time; t.close_px = t.tp1
                closed = True
                self.recent_losses = max(0, self.recent_losses - 1)
            elif held >= timeout_bars:
                t.outcome = "expired"
                t.pnl_r = 0.5 * sf if t.tp1_filled else 0.0
                t.close_idx = idx; t.close_time = bar.time; t.close_px = bar.close
                closed = True

            if closed:
                # Apply slippage friction (deducted from realized P&L)
                if self.slip_per_trade_R > 0:
                    t.pnl_r = t.pnl_r - self.slip_per_trade_R if t.pnl_r > 0 else t.pnl_r - self.slip_per_trade_R
                t.bars_to_resolution = held
                self.trades.append(t)
                self.daily_pnl_r += t.pnl_r
                # Update equity curve
                cum_r = sum(tt.pnl_r for tt in self.trades)
                self.equity_curve.append((bar.time, round(cum_r, 2)))
            else:
                still.append(t)
        self.open_trades = still

    def _summary(self):
        bars_seen = self.bar_count + 1
        n_dec = len(self.decisions)
        n_dir = sum(1 for d in self.decisions if d.direction != 0)
        n_pm = sum(1 for d in self.decisions if d.pm_final >= self.cfg.pm_threshold)
        if not self.trades:
            return {"bars": bars_seen, "trades": 0, "decisions": n_dec,
                    "decisions_with_direction": n_dir, "decisions_pm_passed": n_pm}
        wins = [t for t in self.trades if t.pnl_r > 0]
        losses = [t for t in self.trades if t.pnl_r < 0]
        bes = [t for t in self.trades if t.pnl_r == 0]
        total_r = sum(t.pnl_r for t in self.trades)
        wr = len(wins) / len(self.trades)
        avg_r = total_r / len(self.trades)
        gw = sum(t.pnl_r for t in wins)
        gl = abs(sum(t.pnl_r for t in losses))
        pf = gw / gl if gl > 0 else float('inf')
        cum = peak = max_dd = 0.0
        for t in self.trades:
            cum += t.pnl_r
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        by_setup = {}
        for t in self.trades:
            by_setup.setdefault(t.setup, []).append(t)
        ss = {}
        for s, ts in by_setup.items():
            w = [t for t in ts if t.pnl_r > 0]
            r = sum(t.pnl_r for t in ts)
            ss[s] = {"trades": len(ts), "win_rate": len(w)/len(ts),
                     "avg_r": r/len(ts), "total_r": r}
        by_reg = {}
        for t in self.trades:
            by_reg.setdefault(t.regime, []).append(t)
        rs = {}
        for r_, ts in by_reg.items():
            r = sum(t.pnl_r for t in ts)
            w = [t for t in ts if t.pnl_r > 0]
            rs[r_] = {"trades": len(ts), "win_rate": len(w)/len(ts), "total_r": r}
        return {
            "bars": bars_seen, "trades": len(self.trades),
            "wins": len(wins), "losses": len(losses), "breakevens": len(bes),
            "win_rate": round(wr*100, 1), "total_r": round(total_r, 2),
            "avg_r": round(avg_r, 3),
            "profit_factor": round(pf, 2) if pf != float('inf') else "inf",
            "max_drawdown_r": round(max_dd, 2),
            "by_setup": ss, "by_regime": rs,
            "decisions": n_dec, "decisions_pm_passed": n_pm,
        }


def load_csv(path):
    bars = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t_raw = row.get('time') or row.get('epoch_s')
                if t_raw is None:
                    continue
                vol_raw = row.get('volume', '0') or '0'
                try:
                    vol = float(vol_raw)
                except ValueError:
                    vol = 0.0
                bars.append(Bar(
                    time=int(float(t_raw)),
                    open=float(row['open']), high=float(row['high']),
                    low=float(row['low']), close=float(row['close']),
                    volume=vol,
                ))
            except (KeyError, ValueError):
                continue
    return bars


def main():
    p = argparse.ArgumentParser(description="Apex v2 Firm Backtest (fine-tuned + intermarket)")
    p.add_argument("csv")
    p.add_argument("--pm", type=float, default=40.0)
    p.add_argument("--no-setup-required", action="store_true")
    p.add_argument("--red-weight", type=float, default=1.0)
    # Partials default=True (real-data walk-forward winner 2026-04-16)
    # Use --no-partials to disable (mirrors legacy r_multiple behavior)
    p.add_argument("--use-partials", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--entry-mode", default="pullback", choices=["market", "pullback"])
    p.add_argument("--exit-mode", default="fibonacci",
                   choices=["r_multiple", "alligator", "fibonacci", "hybrid"])
    p.add_argument("--ema-tod", default="Power Hours",
                   choices=["Full Session", "Morning Only", "Avoid Lunch", "Power Hours"])
    p.add_argument("--ema-dow", default="All Days",
                   choices=["All Days", "Skip Thursday", "Skip Thu+Fri", "Mon-Tue Only"])
    # Intermarket data
    p.add_argument("--vix", help="VIX 5m CSV (V8 voice)")
    p.add_argument("--es", help="ES 5m CSV (V9 voice)")
    p.add_argument("--dxy", help="DXY 5m CSV (V10 voice)")
    p.add_argument("--tick", help="TICK 5m CSV (V11 voice)")
    p.add_argument("--no-circuit-breaker", action="store_true",
                   help="Disable daily P&L circuit breaker")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    print(f"Loading {args.csv}...")
    if args.vix or args.es or args.dxy or args.tick:
        from intermarket import load_with_intermarket, coverage_report
        bars = load_with_intermarket(args.csv, vix=args.vix, es=args.es,
                                     dxy=args.dxy, tick=args.tick)
        cov = coverage_report(bars)
        print(f"Loaded {len(bars)} bars  ({datetime.fromtimestamp(bars[0].time)} → {datetime.fromtimestamp(bars[-1].time)})")
        print(f"Intermarket coverage: VIX {cov['with_vix']}/{cov['total_bars']}, "
              f"ES {cov['with_es']}, DXY {cov['with_dxy']}, TICK {cov['with_tick']}")
    else:
        bars = load_csv(args.csv)
        print(f"Loaded {len(bars)} bars  ({datetime.fromtimestamp(bars[0].time)} → {datetime.fromtimestamp(bars[-1].time)})")

    cfg = FirmConfig(pm_threshold=args.pm,
                     require_setup=not args.no_setup_required,
                     redteam_weight=args.red_weight)
    if args.no_circuit_breaker:
        cfg.daily_loss_pause = -100.0  # effectively disabled
        cfg.daily_loss_half_size = -100.0
    det_cfg = V1DetectorConfig(use_partials=args.use_partials,
                               ema_tod_filter=args.ema_tod,
                               ema_dow_filter=args.ema_dow,
                               entry_mode=args.entry_mode,
                               exit_mode=args.exit_mode)
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg)
    s = bt.run(bars)

    print("\n" + "=" * 64)
    print("APEX v2 FIRM BACKTEST  (fine-tuned)")
    print("=" * 64)
    print(f"Bars:           {s.get('bars', 0)}")
    print(f"Trades:         {s.get('trades', 0)}")
    print(f"Blocked by CB:  {bt.blocked_by_circuit_breaker}")
    if s.get('trades', 0) == 0:
        print(f"Decisions:           {s.get('decisions', 0)}")
        print(f"PM passed gate:      {s.get('decisions_pm_passed', 0)}")
        return
    print(f"Wins:           {s['wins']}  ({s['win_rate']}%)")
    print(f"Losses:         {s['losses']}")
    print(f"Breakevens:     {s.get('breakevens', 0)}")
    print(f"Total R:        {s['total_r']:+.2f}")
    print(f"Avg R/trade:    {s['avg_r']:+.3f}")
    print(f"Profit factor:  {s['profit_factor']}")
    print(f"Max DD (R):     {s['max_drawdown_r']}")
    print(f"\n── By Setup ──")
    for setup, st in s['by_setup'].items():
        print(f"  {setup:8s}: {st['trades']:3d} trades  {st['win_rate']*100:5.1f}% win  avg {st['avg_r']:+.2f}R  total {st['total_r']:+.1f}R")
    print(f"\n── By Regime ──")
    for reg, st in s['by_regime'].items():
        print(f"  {reg:10s}: {st['trades']:3d} trades  {st['win_rate']*100:5.1f}% win  total {st['total_r']:+.1f}R")
    if args.verbose:
        print(f"\n── All Trades ──")
        for t in bt.trades:
            dt = datetime.fromtimestamp(t.open_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"  {dt}  {t.side:5s} {t.setup:6s}  PM={t.pm_final:5.1f}  Q={t.quant:+5.1f}  R={t.red:4.0f}  {t.regime:10s}  → {t.outcome:14s} {t.pnl_r:+.2f}R")


if __name__ == "__main__":
    main()
