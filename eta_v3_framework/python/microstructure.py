"""
Microstructure Entry Refinement
================================
Takes 5-minute signal from firm_engine, then uses 1-minute (or 1-second)
data to refine the entry: better price, tighter stop, higher conviction.

Per-strategy micro rules:
  ORB:    Wait for 1m confirmation candle above OR high + close near high
  EMA PB: Wait for 1m rejection candle within the 5m pullback bar
  SWEEP:  Wait for 1m re-test of swept level that holds

Returns MicroEntry with adjusted entry, stop, and confidence score.
"""

from dataclasses import dataclass


@dataclass
class MicroBar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MicroEntry:
    """Result of microstructure refinement for a signal."""

    entered: bool
    entry_price: float
    micro_sl: float  # Tighter stop based on 1m structure
    confidence: float  # 0-1, higher = stronger micro confirmation
    bars_waited: int  # How many 1m bars from signal to entry
    reason: str  # Why entered or skipped
    refined_r_mult: float  # Effective R if using micro_sl instead of original


class MicroEntryRefiner:
    """Refines 5m signals using 1m data within the signal bar."""

    def __init__(
        self,
        tick_size: float = 0.25,
        max_wait_1m_bars: int = 5,
        orb_min_close_pos: float = 0.6,
        ema_min_rejection_wick: float = 0.4,
        sweep_retest_tolerance_ticks: int = 2,
    ):
        self.tick = tick_size
        self.max_wait = max_wait_1m_bars
        self.orb_min_close_pos = orb_min_close_pos
        self.ema_min_rejection_wick = ema_min_rejection_wick
        self.sweep_retest_tol = sweep_retest_tolerance_ticks

    # ─────────────────────────────────────────────────────────────────
    # ORB: 1m confirmation above/below OR extreme + strong close
    # ─────────────────────────────────────────────────────────────────
    def refine_orb(
        self,
        side: str,
        signal_entry: float,
        signal_sl: float,
        or_high: float,
        or_low: float,
        next_1m_bars: list[MicroBar],
    ) -> MicroEntry:
        """ORB micro entry: wait for 1m bar that holds above OR high
        with strong close (>=60% of 1m bar range) for confirmation.
        Tighter stop = below the confirmation 1m bar's low."""
        if not next_1m_bars:
            return MicroEntry(False, signal_entry, signal_sl, 0.0, 0, "no 1m data", 1.0)

        threshold = or_high if side == "long" else or_low
        for i, b in enumerate(next_1m_bars[: self.max_wait]):
            rng = b.high - b.low
            if rng <= 0:
                continue
            close_pos = (b.close - b.low) / rng if side == "long" else (b.high - b.close) / rng

            # Long: need close above OR high AND close in upper portion of bar
            if side == "long":
                held_above = b.close > threshold
                strong_close = close_pos >= self.orb_min_close_pos
                if held_above and strong_close:
                    entry = b.close
                    # Micro SL: below this 1m bar's low - 2 ticks
                    micro_sl = b.low - 2 * self.tick
                    # Original R distance vs micro R distance
                    orig_r_dist = abs(signal_entry - signal_sl)
                    micro_r_dist = abs(entry - micro_sl)
                    refined_r = orig_r_dist / micro_r_dist if micro_r_dist > 0 else 1.0
                    conf = min(1.0, close_pos * 1.2)  # boost for stronger close
                    return MicroEntry(
                        True,
                        entry,
                        micro_sl,
                        conf,
                        i,
                        "1m confirmed above OR+strong close",
                        refined_r,
                    )
            else:  # short
                held_below = b.close < threshold
                strong_close = close_pos >= self.orb_min_close_pos
                if held_below and strong_close:
                    entry = b.close
                    micro_sl = b.high + 2 * self.tick
                    orig_r_dist = abs(signal_entry - signal_sl)
                    micro_r_dist = abs(entry - micro_sl)
                    refined_r = orig_r_dist / micro_r_dist if micro_r_dist > 0 else 1.0
                    conf = min(1.0, close_pos * 1.2)
                    return MicroEntry(
                        True,
                        entry,
                        micro_sl,
                        conf,
                        i,
                        "1m confirmed below OR+strong close",
                        refined_r,
                    )
        # No confirmation within wait window — skip the trade
        return MicroEntry(
            False,
            signal_entry,
            signal_sl,
            0.0,
            self.max_wait,
            "no 1m confirmation within wait window",
            1.0,
        )

    # ─────────────────────────────────────────────────────────────────
    # EMA Pullback: 1m rejection candle within pullback zone
    # ─────────────────────────────────────────────────────────────────
    def refine_ema_pullback(
        self,
        side: str,
        signal_entry: float,
        signal_sl: float,
        ema9: float,
        ema21: float,
        atr: float,
        next_1m_bars: list[MicroBar],
    ) -> MicroEntry:
        """EMA PB micro entry: wait for 1m rejection candle (pin bar or
        engulfing) near EMA zone. Micro SL below rejection candle low."""
        if not next_1m_bars or atr <= 0:
            return MicroEntry(False, signal_entry, signal_sl, 0.0, 0, "no 1m data", 1.0)

        ema_mid = (ema9 + ema21) / 2
        for i, b in enumerate(next_1m_bars[: self.max_wait]):
            rng = b.high - b.low
            if rng <= 0:
                continue
            body = abs(b.close - b.open)
            body / rng

            # Long: look for pin bar at EMA zone (long lower wick, small upper wick)
            if side == "long":
                lower_wick = min(b.open, b.close) - b.low
                upper_wick = b.high - max(b.open, b.close)
                is_pin = lower_wick > upper_wick * 2 and lower_wick > body
                is_bull_close = b.close > b.open
                near_ema = abs(b.low - ema_mid) <= atr * 0.5
                # Engulfing: bullish body engulfs prior bar body
                is_engulf = False
                if i > 0:
                    prev = next_1m_bars[i - 1]
                    is_engulf = (
                        b.close > b.open
                        and prev.close < prev.open
                        and b.open < prev.close
                        and b.close > prev.open
                    )

                if (is_pin or is_engulf) and is_bull_close and near_ema:
                    entry = b.close
                    micro_sl = b.low - 2 * self.tick
                    orig_r_dist = abs(signal_entry - signal_sl)
                    micro_r_dist = abs(entry - micro_sl)
                    refined_r = orig_r_dist / micro_r_dist if micro_r_dist > 0 else 1.0
                    reason = "1m pin at EMA" if is_pin else "1m engulfing at EMA"
                    return MicroEntry(True, entry, micro_sl, 0.85, i, reason, refined_r)
            else:  # short
                upper_wick = b.high - max(b.open, b.close)
                lower_wick = min(b.open, b.close) - b.low
                is_pin = upper_wick > lower_wick * 2 and upper_wick > body
                is_bear_close = b.close < b.open
                near_ema = abs(b.high - ema_mid) <= atr * 0.5
                is_engulf = False
                if i > 0:
                    prev = next_1m_bars[i - 1]
                    is_engulf = (
                        b.close < b.open
                        and prev.close > prev.open
                        and b.open > prev.close
                        and b.close < prev.open
                    )

                if (is_pin or is_engulf) and is_bear_close and near_ema:
                    entry = b.close
                    micro_sl = b.high + 2 * self.tick
                    orig_r_dist = abs(signal_entry - signal_sl)
                    micro_r_dist = abs(entry - micro_sl)
                    refined_r = orig_r_dist / micro_r_dist if micro_r_dist > 0 else 1.0
                    reason = "1m pin at EMA" if is_pin else "1m engulfing at EMA"
                    return MicroEntry(True, entry, micro_sl, 0.85, i, reason, refined_r)
        return MicroEntry(
            False, signal_entry, signal_sl, 0.0, self.max_wait, "no 1m rejection candle", 1.0
        )

    # ─────────────────────────────────────────────────────────────────
    # Sweep: 1m re-test of swept level that holds (doesn't get swept again)
    # ─────────────────────────────────────────────────────────────────
    def refine_sweep(
        self,
        side: str,
        signal_entry: float,
        signal_sl: float,
        swept_level: float,
        next_1m_bars: list[MicroBar],
    ) -> MicroEntry:
        """Sweep micro entry: wait for 1m bar to re-test the reclaimed level
        within tolerance and hold (close back in direction of trade)."""
        if not next_1m_bars:
            return MicroEntry(False, signal_entry, signal_sl, 0.0, 0, "no 1m data", 1.0)

        tol = self.sweep_retest_tol * self.tick
        for i, b in enumerate(next_1m_bars[: self.max_wait]):
            # Long (reclaim of swept low): check 1m bar dips into retest zone but closes above
            if side == "long":
                retest = b.low <= swept_level + tol and b.low > swept_level - tol * 2
                holds = b.close > swept_level + tol
                if retest and holds:
                    entry = b.close
                    micro_sl = swept_level - 3 * self.tick  # Very tight, just below swept level
                    orig_r_dist = abs(signal_entry - signal_sl)
                    micro_r_dist = abs(entry - micro_sl)
                    refined_r = orig_r_dist / micro_r_dist if micro_r_dist > 0 else 1.0
                    return MicroEntry(
                        True, entry, micro_sl, 0.9, i, "1m retest held above swept level", refined_r
                    )
            else:  # short
                retest = b.high >= swept_level - tol and b.high < swept_level + tol * 2
                holds = b.close < swept_level - tol
                if retest and holds:
                    entry = b.close
                    micro_sl = swept_level + 3 * self.tick
                    orig_r_dist = abs(signal_entry - signal_sl)
                    micro_r_dist = abs(entry - micro_sl)
                    refined_r = orig_r_dist / micro_r_dist if micro_r_dist > 0 else 1.0
                    return MicroEntry(
                        True, entry, micro_sl, 0.9, i, "1m retest held below swept level", refined_r
                    )
        return MicroEntry(
            False,
            signal_entry,
            signal_sl,
            0.0,
            self.max_wait,
            "no 1m retest within wait window",
            1.0,
        )


def load_1m_bars(path: str) -> list[MicroBar]:
    """Load 1-minute bars with flexible CSV format."""
    import csv

    bars = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = int(float(row.get("time") or row["epoch_s"]))
                vol_raw = row.get("volume", "0") or "0"
                try:
                    vol = float(vol_raw)
                except ValueError:
                    vol = 0.0
                bars.append(
                    MicroBar(
                        time=t,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=vol,
                    )
                )
            except (KeyError, ValueError):
                continue
    return bars


def get_1m_bars_in_5m_window(
    bars_1m: list[MicroBar], start_time: int, n_bars: int = 5
) -> list[MicroBar]:
    """Get up to n_bars 1m bars starting at start_time."""
    result = []
    for b in bars_1m:
        if b.time >= start_time and len(result) < n_bars:
            result.append(b)
        elif len(result) >= n_bars:
            break
    return result
