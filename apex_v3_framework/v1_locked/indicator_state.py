"""
Indicator State
===============
Streaming computation of technical indicators (ATR, EMA, RSI, ADX, VWAP).
Mirrors Pine Script's incremental computation so backtest results match.

Usage:
    state = IndicatorState()
    for bar in bars:
        state.update(bar)
        # bar.atr, bar.ema9, etc. are now populated
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Deque
import math

from firm_engine import Bar


def _ema(prev: Optional[float], value: float, length: int) -> float:
    alpha = 2.0 / (length + 1)
    return value if prev is None else prev + alpha * (value - prev)


def _rma(prev: Optional[float], value: float, length: int) -> float:
    """Wilder's smoothing (used by RSI / ADX)."""
    alpha = 1.0 / length
    return value if prev is None else prev + alpha * (value - prev)


@dataclass
class IndicatorState:
    atr_period: int = 14
    rsi_period: int = 14
    adx_period: int = 14
    ema_fast: int = 9
    ema_slow: int = 21
    ema_50: int = 50

    # State
    _prev_close: Optional[float] = None
    _atr: Optional[float] = None
    _ema9: Optional[float] = None
    _ema21: Optional[float] = None
    _ema50: Optional[float] = None
    _rsi_avg_gain: Optional[float] = None
    _rsi_avg_loss: Optional[float] = None
    _adx: Optional[float] = None
    _di_plus_smooth: Optional[float] = None
    _di_minus_smooth: Optional[float] = None
    _tr_smooth: Optional[float] = None

    # VWAP (resets daily)
    _current_day: Optional[int] = None
    _vwap_sum_pv: float = 0.0
    _vwap_sum_v: float = 0.0

    # Rolling windows for computed-from-history features
    _adx_history: Deque[float] = field(default_factory=lambda: deque(maxlen=10))
    _atr_history: Deque[float] = field(default_factory=lambda: deque(maxlen=20))
    _vol_history: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    _range_history: Deque[float] = field(default_factory=lambda: deque(maxlen=20))
    _high_history: Deque[float] = field(default_factory=lambda: deque(maxlen=10))
    _low_history: Deque[float] = field(default_factory=lambda: deque(maxlen=10))

    # Daily prev values
    _prev_day_high: Optional[float] = None
    _prev_day_low: Optional[float] = None
    _today_high: Optional[float] = None
    _today_low: Optional[float] = None
    _today_open: Optional[float] = None

    # 15m HTF aggregator (5-min bars → 15-min)
    _htf_bars_in_window: int = 0
    _htf_high: Optional[float] = None
    _htf_low: Optional[float] = None
    _htf_close: Optional[float] = None
    _htf_ema50: Optional[float] = None

    def _update_vwap(self, bar: Bar) -> None:
        day = bar.time // 86400
        if self._current_day != day:
            self._current_day = day
            self._vwap_sum_pv = 0.0
            self._vwap_sum_v = 0.0
            # New day rollover
            if self._today_high is not None:
                self._prev_day_high = self._today_high
                self._prev_day_low = self._today_low
            self._today_high = bar.high
            self._today_low = bar.low
            self._today_open = bar.open
        else:
            self._today_high = max(self._today_high, bar.high) if self._today_high else bar.high
            self._today_low = min(self._today_low, bar.low) if self._today_low else bar.low
        hlc3 = (bar.high + bar.low + bar.close) / 3.0
        self._vwap_sum_pv += hlc3 * bar.volume
        self._vwap_sum_v += bar.volume
        bar.vwap = self._vwap_sum_pv / self._vwap_sum_v if self._vwap_sum_v > 0 else None

    def _update_atr(self, bar: Bar) -> None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(bar.high - bar.low,
                     abs(bar.high - self._prev_close),
                     abs(bar.low - self._prev_close))
        self._atr = _rma(self._atr, tr, self.atr_period)
        bar.atr = self._atr
        self._atr_history.append(self._atr)

    def _update_emas(self, bar: Bar) -> None:
        self._ema9 = _ema(self._ema9, bar.close, self.ema_fast)
        self._ema21 = _ema(self._ema21, bar.close, self.ema_slow)
        self._ema50 = _ema(self._ema50, bar.close, self.ema_50)
        bar.ema9 = self._ema9
        bar.ema21 = self._ema21
        bar.ema50 = self._ema50

    def _update_rsi(self, bar: Bar) -> None:
        if self._prev_close is None:
            bar.rsi = 50.0
            return
        change = bar.close - self._prev_close
        gain = max(change, 0)
        loss = abs(min(change, 0))
        self._rsi_avg_gain = _rma(self._rsi_avg_gain, gain, self.rsi_period)
        self._rsi_avg_loss = _rma(self._rsi_avg_loss, loss, self.rsi_period)
        if self._rsi_avg_loss is None or self._rsi_avg_loss == 0:
            bar.rsi = 100.0 if (self._rsi_avg_gain or 0) > 0 else 50.0
        else:
            rs = (self._rsi_avg_gain or 0) / self._rsi_avg_loss
            bar.rsi = 100 - (100 / (1 + rs))

    def _update_adx(self, bar: Bar) -> None:
        if self._prev_close is None:
            bar.adx = 20.0
            return
        up_move = bar.high - (bar.high if not hasattr(self, '_prev_high') or self._prev_high is None else self._prev_high)
        down_move = (bar.low if not hasattr(self, '_prev_low') or self._prev_low is None else self._prev_low) - bar.low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(bar.high - bar.low,
                 abs(bar.high - self._prev_close),
                 abs(bar.low - self._prev_close))
        self._tr_smooth = _rma(self._tr_smooth, tr, self.adx_period)
        self._di_plus_smooth = _rma(self._di_plus_smooth, plus_dm, self.adx_period)
        self._di_minus_smooth = _rma(self._di_minus_smooth, minus_dm, self.adx_period)
        if self._tr_smooth and self._tr_smooth > 0:
            di_plus = 100 * (self._di_plus_smooth or 0) / self._tr_smooth
            di_minus = 100 * (self._di_minus_smooth or 0) / self._tr_smooth
            di_sum = di_plus + di_minus
            dx = 100 * abs(di_plus - di_minus) / di_sum if di_sum > 0 else 0
            self._adx = _rma(self._adx, dx, self.adx_period)
            bar.adx = self._adx
        else:
            bar.adx = 20.0
        self._adx_history.append(bar.adx or 20.0)

    def _update_htf(self, bar: Bar) -> None:
        """Aggregate 5-min bars into 15-min HTF for V6 voice."""
        # Use bar time to determine 15-min window (3 bars per window for 5m timeframe)
        self._htf_bars_in_window += 1
        if self._htf_high is None:
            self._htf_high = bar.high
            self._htf_low = bar.low
        else:
            self._htf_high = max(self._htf_high, bar.high)
            self._htf_low = min(self._htf_low, bar.low)
        self._htf_close = bar.close
        if self._htf_bars_in_window >= 3:
            self._htf_ema50 = _ema(self._htf_ema50, self._htf_close, 50)
            self._htf_bars_in_window = 0
            self._htf_high = None
            self._htf_low = None
        bar.htf_close = self._htf_close
        bar.htf_ema50 = self._htf_ema50

    # Williams Alligator (Bill Williams) - 3 SMMA's displaced into future
    # Jaw: SMMA(13, +8 bars), Teeth: SMMA(8, +5 bars), Lips: SMMA(5, +3 bars)
    # Used for trend-end detection (price closing back through Lips = momentum stall)
    _alligator_jaw_buf: Deque[float] = field(default_factory=lambda: deque(maxlen=21))
    _alligator_teeth_buf: Deque[float] = field(default_factory=lambda: deque(maxlen=13))
    _alligator_lips_buf: Deque[float] = field(default_factory=lambda: deque(maxlen=8))
    _alligator_jaw: Optional[float] = None
    _alligator_teeth: Optional[float] = None
    _alligator_lips: Optional[float] = None

    def _update_alligator(self, bar: Bar) -> None:
        """SMMA = Wilder's smoothing on median price. Displaced shift handled
        by reading historical buffer values."""
        median = (bar.high + bar.low) / 2.0
        # Smoothed Moving Average via Wilder's RMA
        self._alligator_jaw = _rma(self._alligator_jaw, median, 13)
        self._alligator_teeth = _rma(self._alligator_teeth, median, 8)
        self._alligator_lips = _rma(self._alligator_lips, median, 5)
        self._alligator_jaw_buf.append(self._alligator_jaw or median)
        self._alligator_teeth_buf.append(self._alligator_teeth or median)
        self._alligator_lips_buf.append(self._alligator_lips or median)
        # Expose displaced (look-back) values as bar attributes
        # Lips displaced 3 bars (use value from 3 bars ago)
        bar.alligator_lips = self._alligator_lips_buf[-4] if len(self._alligator_lips_buf) >= 4 else self._alligator_lips
        bar.alligator_teeth = self._alligator_teeth_buf[-6] if len(self._alligator_teeth_buf) >= 6 else self._alligator_teeth
        bar.alligator_jaw = self._alligator_jaw_buf[-9] if len(self._alligator_jaw_buf) >= 9 else self._alligator_jaw

    def update(self, bar: Bar) -> Bar:
        """Compute all indicators for this bar in-place."""
        self._update_vwap(bar)
        self._update_atr(bar)
        self._update_emas(bar)
        self._update_rsi(bar)
        self._update_adx(bar)
        self._update_htf(bar)
        self._update_alligator(bar)

        self._vol_history.append(bar.volume)
        self._range_history.append(bar.high - bar.low)
        self._high_history.append(bar.high)
        self._low_history.append(bar.low)

        # Track for next iteration
        self._prev_close = bar.close
        self._prev_high = bar.high
        self._prev_low = bar.low
        return bar

    # ── Helper accessors used by Firm engine ──
    def atr_ma20(self) -> float:
        if not self._atr_history:
            return 0.0
        return sum(self._atr_history) / len(self._atr_history)

    def vol_z(self) -> float:
        if len(self._vol_history) < 5:
            return 0.0
        mean = sum(self._vol_history) / len(self._vol_history)
        var = sum((v - mean) ** 2 for v in self._vol_history) / len(self._vol_history)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return (self._vol_history[-1] - mean) / std

    def vol_z_at(self, lookback: int) -> float:
        """vol_z computed as if the last `lookback` bars hadn't happened yet."""
        if len(self._vol_history) < lookback + 5:
            return 0.0
        sample = list(self._vol_history)[:-lookback] if lookback > 0 else list(self._vol_history)
        if len(sample) < 5:
            return 0.0
        mean = sum(sample) / len(sample)
        var = sum((v - mean) ** 2 for v in sample) / len(sample)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return (sample[-1] - mean) / std

    def range_avg_20(self) -> float:
        if not self._range_history:
            return 0.0
        return sum(self._range_history) / len(self._range_history)

    def adx_3_bars_ago(self) -> float:
        if len(self._adx_history) < 4:
            return self._adx_history[0] if self._adx_history else 20.0
        return list(self._adx_history)[-4]

    def highest_5_prev(self) -> float:
        if len(self._high_history) < 6:
            return max(self._high_history) if self._high_history else 0.0
        return max(list(self._high_history)[-6:-1])

    def lowest_5_prev(self) -> float:
        if len(self._low_history) < 6:
            return min(self._low_history) if self._low_history else 0.0
        return min(list(self._low_history)[-6:-1])

    @property
    def prev_day_high(self) -> Optional[float]:
        return self._prev_day_high

    @property
    def prev_day_low(self) -> Optional[float]:
        return self._prev_day_low
