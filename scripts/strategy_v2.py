"""ScriptedStrategyV2 — parameterized improvements over the v1 EMA-cross scalper.

Levers exposed through :class:`StrategyConfig`:

* **vol_filter_stdev_max** — block entries when a rolling close-to-close stdev
  exceeds this threshold. v1 loses money in the ``high_vol`` regime (27 % WR
  on 11 trades = -$43). Filtering that regime out is the single biggest PnL
  lever we have. v2 estimates volatility from the bar stream itself (no
  regime-label cheating).
* **trend_align_bars** — require the longer EMA's slope over the last N bars
  to agree with the intended trade side. This filters out counter-trend
  fake crosses that happen in noisy chop.
* **rr** — risk-reward. v1 uses 1.5R. 2.0R/2.5R are worth testing given how
  often v1 hits TP in trend regimes.
* **risk_ticks** — stop distance. Keeping this at 12 for comparability.
* **morning_window / afternoon_window** — bar-index ranges where entries are
  permitted. v1 only fires in the afternoon; v2 can also fire on the morning
  drive which is where trend_up / trend_down make the cleanest moves.
* **loss_cooldown_bars** — extra cooldown after a losing trade so we don't
  immediately re-enter the same whipsaw.
* **cross_magnitude_min** — require the EMA9–EMA21 spread to exceed this
  many points on the signal bar, filtering weak crossings.
* **orderflow_proxy_min** — bar-based order-flow proxy. For a LONG signal
  we require ``(close-low) / range`` on the signal bar to exceed this
  threshold (i.e. the bar closed on the aggressive-buy side). For SHORT
  we require ``(high-close) / range``. 0.0 disables. This is a zero-cost
  proxy for actual delta that works on bar data alone.

Each variant we test is just a different ``StrategyConfig``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from mnq.core.types import Bar, OrderType, Side, Signal, quantize_to_tick


@dataclass
class StrategyConfig:
    """Tuning knobs for ScriptedStrategyV2."""

    name: str = "v2"
    # Risk management
    risk_ticks: int = 12
    rr: float = 1.5
    time_stop_bars: int = 20

    # Vol filter (0.0 = disabled)
    vol_lookback_bars: int = 20
    vol_filter_stdev_max: float = 0.0  # 0 → off

    # Trend alignment filter (0 = disabled)
    trend_align_bars: int = 0

    # Cross-magnitude filter
    cross_magnitude_min: float = 0.3

    # Order-flow proxy: require close to be in the aggressive portion of the bar.
    # 0.0 disables; 0.55 ≈ "close above mid"; 0.70 ≈ "close in top 30% of range".
    orderflow_proxy_min: float = 0.0

    # Hard-pause regime: if realized stdev exceeds this threshold, ALWAYS skip
    # (no trades, not even counter-signals). 0.0 disables.  Paired with
    # vol_filter_stdev_max this lets us express "block above X, pause above Y".
    vol_hard_pause_stdev: float = 0.0

    # Percentage-based vol filter: stdev / close > threshold → block.
    # Normalizes for price level so the filter works across 7k→26k MNQ.
    # 0.0 disables.  Replaces absolute stdev filter when set.
    vol_filter_pct_max: float = 0.0
    vol_hard_pause_pct: float = 0.0

    # Entry windows (bar indices within a 390-bar RTH day)
    morning_window: tuple[int, int] | None = None  # e.g. (15, 120)
    afternoon_window: tuple[int, int] | None = (270, 375)

    # Cooldown after flat
    cooldown_bars: int = 3
    loss_cooldown_bars: int = 3  # EXTRA cooldown if last trade was a loss

    # EMA spans
    ema_fast: int = 9
    ema_slow: int = 21

    # Directional bias — gate one side off entirely.
    # allow_long / allow_short default to True for backwards compat.
    allow_long: bool = True
    allow_short: bool = True


@dataclass
class _VolEstimator:
    """Rolling close-to-close stdev, lightweight (no numpy)."""

    lookback: int
    _diffs: deque[float] = field(default_factory=deque)
    _last_close: float | None = None

    def update(self, close: float) -> None:
        if self._last_close is not None:
            self._diffs.append(close - self._last_close)
            while len(self._diffs) > self.lookback:
                self._diffs.popleft()
        self._last_close = close

    def stdev(self) -> float:
        n = len(self._diffs)
        if n < 2:
            return 0.0
        mean = sum(self._diffs) / n
        var = sum((d - mean) ** 2 for d in self._diffs) / (n - 1)
        return var ** 0.5


class ScriptedStrategyV2:
    """Duck-types as StrategyBase (on_bar / update_position).

    Internally tracks EMA(fast) / EMA(slow), a rolling vol estimator, and an
    EMA-slow slope history ring for trend-alignment checks.
    """

    def __init__(self, spec: Any, *, cfg: StrategyConfig | None = None) -> None:
        self.spec = spec
        self.cfg = cfg or StrategyConfig()
        self._tick = Decimal(str(spec.instrument.tick_size))

        # EMAs
        self._kf = 2.0 / (self.cfg.ema_fast + 1)
        self._ks = 2.0 / (self.cfg.ema_slow + 1)
        self._e_fast: float | None = None
        self._e_slow: float | None = None
        self._last_diff: float | None = None

        # Slow-EMA history for trend alignment
        self._slow_hist: deque[float] = deque(maxlen=max(2, self.cfg.trend_align_bars + 1))

        # Vol
        self._vol = _VolEstimator(lookback=self.cfg.vol_lookback_bars)

        # State
        self._position = 0
        self._bar_ix = -1
        self._cooldown = 0
        self._last_trade_was_loss = False
        self._entry_price_for_outcome: Decimal | None = None
        self._entry_side_for_outcome: Side | None = None

    # -- Position callback ----------------------------------------------------

    def update_position(self, q: int) -> None:
        """Called by the engine/harness whenever position size changes.

        When we go flat we: (a) arm the cooldown, and (b) check whether the
        just-closed trade was a loser (to lengthen the cooldown).
        """
        if q == 0 and self._position != 0:
            cd = self.cfg.cooldown_bars
            if self._last_trade_was_loss:
                cd += self.cfg.loss_cooldown_bars
            self._cooldown = cd
        self._position = q

    def report_trade_outcome(self, *, pnl_dollars: Decimal) -> None:
        """Optional: the harness tells us whether the last trade won or lost.

        If the harness never calls this, ``_last_trade_was_loss`` stays
        ``False`` and the loss-cooldown simply never activates.
        """
        self._last_trade_was_loss = pnl_dollars < 0

    # -- Bar callback ---------------------------------------------------------

    @staticmethod
    def _orderflow_proxy(bar: Bar, side: Side) -> float:
        """Return a 0..1 score for how aggressive the bar closed on ``side``.

        For LONG: (close - low) / range — 1.0 means closed at the high.
        For SHORT: (high - close) / range — 1.0 means closed at the low.
        If range==0 the bar is a doji; return 0.5 (neutral).
        """
        hi = float(bar.high)
        lo = float(bar.low)
        cl = float(bar.close)
        rng = hi - lo
        if rng <= 0:
            return 0.5
        if side is Side.LONG:
            return (cl - lo) / rng
        return (hi - cl) / rng

    def on_bar(self, bar: Bar) -> Signal | None:
        self._bar_ix += 1
        c = float(bar.close)

        # Maintain features unconditionally.
        self._e_fast = c if self._e_fast is None else (self._e_fast + self._kf * (c - self._e_fast))
        self._e_slow = c if self._e_slow is None else (self._e_slow + self._ks * (c - self._e_slow))
        self._slow_hist.append(self._e_slow)
        self._vol.update(c)
        diff = self._e_fast - self._e_slow
        prev = self._last_diff
        self._last_diff = diff

        # Cooldown countdown (still advance features during cooldown).
        if self._cooldown > 0:
            self._cooldown -= 1
            return None

        # Only emit if flat.
        if self._position != 0:
            return None

        # Need history for a cross test.
        if prev is None:
            return None

        # Session-window gate (bar index based).
        if not self._in_window(self._bar_ix):
            return None

        # Cross detection.
        want_side: Side | None = None
        if prev <= 0 and diff > self.cfg.cross_magnitude_min:
            want_side = Side.LONG
        elif prev >= 0 and diff < -self.cfg.cross_magnitude_min:
            want_side = Side.SHORT
        if want_side is None:
            return None

        # 0. Directional bias gate — drop disallowed sides before any other work.
        if want_side is Side.LONG and not self.cfg.allow_long:
            return None
        if want_side is Side.SHORT and not self.cfg.allow_short:
            return None

        # --- Filter gauntlet -------------------------------------------------

        sd = self._vol.stdev()

        # Percentage-based vol (normalized by price level).
        sd_pct = sd / c * 100 if c > 0 else 0.0

        # 1a. Hard-pause regime: if realized vol exceeds this, pause everything.
        if self.cfg.vol_hard_pause_pct > 0.0 and sd_pct > self.cfg.vol_hard_pause_pct:
            return None
        if self.cfg.vol_hard_pause_stdev > 0.0 and sd > self.cfg.vol_hard_pause_stdev:
            return None

        # 1b. Vol filter — skip if realized vol is too high (i.e. high_vol regime).
        if self.cfg.vol_filter_pct_max > 0.0 and sd_pct > self.cfg.vol_filter_pct_max:
            return None
        if self.cfg.vol_filter_stdev_max > 0.0 and sd > self.cfg.vol_filter_stdev_max:
            return None

        # 2. Trend alignment — require slow-EMA slope to agree with direction.
        if self.cfg.trend_align_bars > 0 and len(self._slow_hist) > self.cfg.trend_align_bars:
            slope = self._slow_hist[-1] - self._slow_hist[-1 - self.cfg.trend_align_bars]
            if want_side is Side.LONG and slope <= 0:
                return None
            if want_side is Side.SHORT and slope >= 0:
                return None

        # 3. Order-flow proxy — require the signal bar to close aggressively
        # on the intended side. This is a free filter using only OHLC.
        if self.cfg.orderflow_proxy_min > 0.0:
            score = self._orderflow_proxy(bar, want_side)
            if score < self.cfg.orderflow_proxy_min:
                return None

        # All filters passed — build the signal.
        return self._make_signal(bar, want_side)

    # -- helpers --------------------------------------------------------------

    def _in_window(self, ix: int) -> bool:
        hit = False
        if self.cfg.morning_window is not None:
            lo, hi = self.cfg.morning_window
            hit = hit or (lo <= ix <= hi)
        if self.cfg.afternoon_window is not None:
            lo, hi = self.cfg.afternoon_window
            hit = hit or (lo <= ix <= hi)
        return hit

    def _make_signal(self, bar: Bar, side: Side) -> Signal:
        ref = quantize_to_tick(bar.close, self._tick)
        risk_pts = self._tick * self.cfg.risk_ticks
        reward_pts = risk_pts * Decimal(str(self.cfg.rr))
        if side is Side.LONG:
            stop = quantize_to_tick(ref - risk_pts, self._tick)
            tp = quantize_to_tick(ref + reward_pts, self._tick)
        else:
            stop = quantize_to_tick(ref + risk_pts, self._tick)
            tp = quantize_to_tick(ref - reward_pts, self._tick)
        self._entry_price_for_outcome = ref
        self._entry_side_for_outcome = side
        return Signal(
            side=side,
            qty=1,
            ref_price=ref,
            stop=stop,
            take_profit=tp,
            order_type=OrderType.MARKET,
            limit_offset_ticks=0,
            market_fallback_ms=500,
            time_stop_bars=self.cfg.time_stop_bars,
            spec_hash="",
            spec_semver=f"scripted_v2:{self.cfg.name}",
        )


# ---------------------------------------------------------------------------
# Variant registry — the knobs we'll A/B test.
# ---------------------------------------------------------------------------

VARIANTS: list[StrategyConfig] = [
    # Baseline: same knobs as v1 — proves we can reproduce v1 behaviour.
    StrategyConfig(
        name="v1_replica",
        rr=1.5,
        vol_filter_stdev_max=0.0,
        trend_align_bars=0,
        cross_magnitude_min=0.3,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=0,
    ),
    # v2a: add vol filter only.
    StrategyConfig(
        name="v2a_volfilter",
        rr=1.5,
        vol_filter_stdev_max=2.4,
        trend_align_bars=0,
        cross_magnitude_min=0.3,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=0,
    ),
    # v2b: vol filter + trend alignment.
    StrategyConfig(
        name="v2b_volfilter_trend",
        rr=1.5,
        vol_filter_stdev_max=2.4,
        trend_align_bars=5,
        cross_magnitude_min=0.3,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=2,
    ),
    # v2c: v2b + morning window (more trades in trend regimes).
    StrategyConfig(
        name="v2c_volfilter_trend_morning",
        rr=1.5,
        vol_filter_stdev_max=2.4,
        trend_align_bars=5,
        cross_magnitude_min=0.3,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=2,
    ),
    # v2d: v2c with wider 2.5R target.
    StrategyConfig(
        name="v2d_wide_target",
        rr=2.5,
        vol_filter_stdev_max=2.4,
        trend_align_bars=5,
        cross_magnitude_min=0.3,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=2,
    ),
    # v2e: v2c with tighter stop and 2R target (same $ risk, different shape).
    StrategyConfig(
        name="v2e_tight_stop",
        risk_ticks=8,
        rr=2.0,
        vol_filter_stdev_max=2.4,
        trend_align_bars=5,
        cross_magnitude_min=0.3,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=2,
    ),
    # v2f: v2c with stronger cross-magnitude filter (require conviction).
    StrategyConfig(
        name="v2f_strict_cross",
        rr=1.5,
        vol_filter_stdev_max=2.4,
        trend_align_bars=5,
        cross_magnitude_min=0.6,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=2,
    ),
    # v2g: v2c + order-flow proxy (close in top 60% of bar).
    StrategyConfig(
        name="v2g_orderflow",
        rr=1.5,
        vol_filter_stdev_max=2.4,
        trend_align_bars=5,
        cross_magnitude_min=0.3,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=2,
    ),
    # v2h: v2c + HARD pause in very high vol (stdev > 3.5), soft filter at 2.4.
    StrategyConfig(
        name="v2h_hard_pause",
        rr=1.5,
        vol_filter_stdev_max=2.4,
        vol_hard_pause_stdev=3.5,
        trend_align_bars=5,
        cross_magnitude_min=0.3,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    # v2i: kitchen sink — all filters on.  If this beats v2c, the order-flow
    # + hard-pause gates stack cleanly; if it loses to v2c we know we filtered
    # too aggressively and v2g / v2h alone are the frontier.
    StrategyConfig(
        name="v2i_full_stack",
        rr=2.0,
        risk_ticks=10,
        vol_filter_stdev_max=2.4,
        vol_hard_pause_stdev=3.5,
        trend_align_bars=5,
        cross_magnitude_min=0.4,
        orderflow_proxy_min=0.55,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    # -----------------------------------------------------------------------
    # Real-data-calibrated variants.  Real 1m MNQ RTH bars have:
    #   - median close-to-close stdev ~ 13 pts  (p75 ~ 17, p95 ~ 28)
    #   - typical bar range ~ 10-30 pts
    # The synthetic-calibrated stops (12 ticks = 3 pts) and thresholds
    # (2.4 stdev, 0.3 cross) are meaningless at that scale.  These
    # "r_*" variants are tuned for real-data magnitudes.
    # -----------------------------------------------------------------------
    # r0: baseline for real data — loose filter, wide stop, afternoon only.
    StrategyConfig(
        name="r0_real_baseline",
        rr=1.5,
        risk_ticks=40,       # 10 pts — survives normal 1m noise
        time_stop_bars=20,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=0.0,
        trend_align_bars=0,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=0,
    ),
    # r1: + vol filter calibrated to real p75 (block very noisy bars).
    StrategyConfig(
        name="r1_real_volfilter",
        rr=1.5,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        trend_align_bars=0,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=2,
    ),
    # r2: + trend alignment + morning window (trade the drive).
    StrategyConfig(
        name="r2_real_trend_morn",
        rr=1.5,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        trend_align_bars=5,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=2,
    ),
    # r3: + hard pause at p95 stdev.
    StrategyConfig(
        name="r3_real_hard_pause",
        rr=1.5,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    # r4: r3 + order-flow proxy (close in aggressive 60% of bar).
    StrategyConfig(
        name="r4_real_orderflow",
        rr=1.5,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    # r5: r4 with wider 2R target.
    StrategyConfig(
        name="r5_real_wide_target",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    # r6: r4 with full-day window (all-session but filters stay tight).
    StrategyConfig(
        name="r6_real_allday",
        rr=1.5,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(0, 389),
        afternoon_window=None,
        loss_cooldown_bars=3,
    ),
    # r7: r4 with tighter cross (conviction only) + strict orderflow (0.70).
    StrategyConfig(
        name="r7_real_conviction",
        rr=1.5,
        risk_ticks=40,
        cross_magnitude_min=4.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.70,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    # -----------------------------------------------------------------------
    # 5-minute timeframe variants (78 bars / RTH day).
    # Morning window first 12 bars = 09:30-10:30; afternoon last 18 = 14:30-15:59.
    # 5m bar realized stdev ~ 12 pts median, p90 ~ 25 pts.  Stops set wider.
    # -----------------------------------------------------------------------
    StrategyConfig(
        name="m5_0_baseline",
        rr=1.5,
        risk_ticks=60,           # 15 pts
        time_stop_bars=10,       # 50 minutes
        cross_magnitude_min=3.0,
        trend_align_bars=0,
        vol_filter_stdev_max=0.0,
        morning_window=(2, 18),
        afternoon_window=(58, 77),
        loss_cooldown_bars=1,
    ),
    StrategyConfig(
        name="m5_1_volfilter_trend",
        rr=1.5,
        risk_ticks=60,
        time_stop_bars=10,
        cross_magnitude_min=3.0,
        trend_align_bars=4,
        vol_filter_stdev_max=20.0,
        morning_window=(2, 18),
        afternoon_window=(58, 77),
        loss_cooldown_bars=2,
    ),
    StrategyConfig(
        name="m5_2_all_filters",
        rr=1.5,
        risk_ticks=60,
        time_stop_bars=10,
        cross_magnitude_min=3.0,
        trend_align_bars=4,
        vol_filter_stdev_max=20.0,
        vol_hard_pause_stdev=35.0,
        orderflow_proxy_min=0.60,
        morning_window=(2, 18),
        afternoon_window=(58, 77),
        loss_cooldown_bars=2,
    ),
    StrategyConfig(
        name="m5_3_wide_2r",
        rr=2.0,
        risk_ticks=60,
        time_stop_bars=10,
        cross_magnitude_min=3.0,
        trend_align_bars=4,
        vol_filter_stdev_max=20.0,
        vol_hard_pause_stdev=35.0,
        orderflow_proxy_min=0.60,
        morning_window=(2, 18),
        afternoon_window=(58, 77),
        loss_cooldown_bars=2,
    ),
    StrategyConfig(
        name="m5_4_allday",
        rr=1.5,
        risk_ticks=60,
        time_stop_bars=10,
        cross_magnitude_min=3.0,
        trend_align_bars=4,
        vol_filter_stdev_max=20.0,
        vol_hard_pause_stdev=35.0,
        orderflow_proxy_min=0.60,
        morning_window=(0, 77),
        afternoon_window=None,
        loss_cooldown_bars=2,
    ),
    # -----------------------------------------------------------------------
    # Perturbation cluster around r5_real_wide_target (the current winner).
    # Each t* variant flips ONE knob so we can attribute any delta cleanly.
    # r5 baseline:
    #   rr=2.0, risk_ticks=40, cross_magnitude_min=2.0,
    #   vol_filter_stdev_max=17.0, vol_hard_pause_stdev=28.0,
    #   trend_align_bars=5, orderflow_proxy_min=0.60,
    #   morning=(30,120), afternoon=(270,375), loss_cooldown=3.
    # -----------------------------------------------------------------------
    StrategyConfig(
        name="t0_r5_tight_stop",          # risk_ticks 40 -> 32 (8 pts)
        rr=2.0,
        risk_ticks=32,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t1_r5_wide_stop",           # risk_ticks 40 -> 48 (12 pts)
        rr=2.0,
        risk_ticks=48,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t2_r5_rr25",                # rr 2.0 -> 2.5
        rr=2.5,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t3_r5_rr175",               # rr 2.0 -> 1.75 (easier target)
        rr=1.75,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t4_r5_tight_cross",         # cross_magnitude_min 2.0 -> 3.0
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=3.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t5_r5_loose_flow",          # orderflow_proxy 0.60 -> 0.50
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.50,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t6_r5_strict_flow",         # orderflow_proxy 0.60 -> 0.70
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.70,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t7_r5_morning_only",        # kill afternoon window
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=None,
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t8_r5_afternoon_only",      # kill morning window
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t9_r5_short_hold",          # time_stop_bars 20 -> 12
        rr=2.0,
        risk_ticks=40,
        time_stop_bars=12,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t10_r5_long_hold",          # time_stop_bars 20 -> 35
        rr=2.0,
        risk_ticks=40,
        time_stop_bars=35,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t11_r5_no_cooldown",        # loss_cooldown 3 -> 0
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=0,
    ),
    StrategyConfig(
        name="t12_r5_pm_wide",            # afternoon-only, widen window 240..380
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=None,
        afternoon_window=(240, 380),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t13_r5_pm_loose_cross",     # afternoon-only + cross 1.5 (more trades)
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=1.5,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t14_r5_pm_loose_flow",      # afternoon-only + flow 0.50
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.50,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="t15_r5_pm_no_volcap",       # afternoon-only + drop hard vol cap
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=22.0,
        vol_hard_pause_stdev=0.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=None,
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    # r5 + directional-bias experiments. On 15 days of real MNQ the journal
    # showed longs 5/5/+$12.30, shorts 3/3/-$5.22 AND +0.53 ticks more slippage
    # on shorts. Gate out the losing direction and keep everything else equal.
    StrategyConfig(
        name="t16_r5_long_only",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
        allow_long=True,
        allow_short=False,
    ),
    StrategyConfig(
        name="t17_r5_short_only",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_stdev_max=17.0,
        vol_hard_pause_stdev=28.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
        allow_long=False,
        allow_short=True,
    ),
    # -----------------------------------------------------------------------
    # Batch 12: Normalized-vol variants.
    # The absolute stdev filter (17.0) was calibrated to 2019 prices (~7,700).
    # At 2022+ prices (12k-26k), normal 1m stdev scales linearly with price,
    # so the filter blocks ALL trades.  These n* variants use pct-based vol
    # filter: stdev / close × 100.
    #
    # Calibration: stdev 17 / price 7700 = 0.22%.  p75 = 0.30%.
    # At 26k: 0.22% → stdev ~57.  0.30% → stdev ~78.
    # -----------------------------------------------------------------------
    StrategyConfig(
        name="n0_pct_baseline",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_pct_max=0.22,
        vol_hard_pause_pct=0.36,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="n1_pct_loose",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_pct_max=0.30,
        vol_hard_pause_pct=0.50,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="n2_pct_long_only",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_pct_max=0.22,
        vol_hard_pause_pct=0.36,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
        allow_long=True,
        allow_short=False,
    ),
    StrategyConfig(
        name="n3_pct_morning_only",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_pct_max=0.22,
        vol_hard_pause_pct=0.36,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=None,
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="n4_pct_no_vol",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="n5_pct_long_morning",
        rr=2.0,
        risk_ticks=40,
        cross_magnitude_min=2.0,
        vol_filter_pct_max=0.22,
        vol_hard_pause_pct=0.36,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=None,
        loss_cooldown_bars=3,
        allow_long=True,
        allow_short=False,
    ),
    # Adaptive risk: scale stop distance with price level.
    # 40 ticks at 7700 = 10 pts = 0.13% of price.
    # At 26000: 0.13% → 33.8 pts → 135 ticks.  Use 120 as round number.
    StrategyConfig(
        name="n6_pct_adaptive_stop",
        rr=2.0,
        risk_ticks=120,
        cross_magnitude_min=5.0,  # ~0.025% at 20k
        vol_filter_pct_max=0.22,
        vol_hard_pause_pct=0.36,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
    ),
    StrategyConfig(
        name="n7_pct_adaptive_long",
        rr=2.0,
        risk_ticks=120,
        cross_magnitude_min=5.0,
        vol_filter_pct_max=0.22,
        vol_hard_pause_pct=0.36,
        trend_align_bars=5,
        orderflow_proxy_min=0.60,
        morning_window=(30, 120),
        afternoon_window=(270, 375),
        loss_cooldown_bars=3,
        allow_long=True,
        allow_short=False,
    ),
]


__all__ = ["ScriptedStrategyV2", "StrategyConfig", "VARIANTS"]
