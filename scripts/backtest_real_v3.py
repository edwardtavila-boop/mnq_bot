"""Batch 13 — Apex V3 15-voice engine on real Databento MNQ tape.

Wires the Apex V3 signal system (V1Detector + 15-voice Firm engine) into the
7-year Databento 1m tape. The V3 system was designed for 5m bars, so we:

  1. Load 1m Databento bars via load_databento_days()
  2. Aggregate 1m → 5m bars for the V3 detector
  3. Run V1Detector + firm_engine.evaluate() per day on 5m bars
  4. Resolve entries/exits on 1m bars for tick-level precision
  5. Output reports in the same format as backtest_real.py

Intermarket voices (V8-V11: VIX/ES/DXY/TICK) return 0 — no sibling data
in the 1m tape. This is conservative: real deployment will have these feeds.

Output:
    reports/backtest_real_v3.md          — per-variant summary
    reports/backtest_real_v3_trades.csv  — full trade log
    data/backtest_real_v3_daily.json     — per-day PnL (for gate revalidation)

Usage:
    python scripts/backtest_real_v3.py
    python scripts/backtest_real_v3.py --max-days 200
    python scripts/backtest_real_v3.py --pm 35 --no-partials
    python scripts/backtest_real_v3.py --exit-mode r_multiple
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc  # noqa: UP017

if not hasattr(_dt, "UTC"):
    _dt.UTC = timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = Path(__file__).resolve().parent
V3_DIR = REPO_ROOT / "eta_v3_framework" / "python"

for p in (str(SRC), str(SCRIPTS), str(V3_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from real_bars import load_databento_days  # noqa: E402
from mnq.core.types import Bar as MnqBar  # noqa: E402

# V3 imports — these come from eta_v3_framework/python/
from firm_engine import (  # noqa: E402
    Bar as V3Bar,
    SetupTriggers,
    FirmConfig,
    FirmDecision,
    detect_regime,
    evaluate,
)
from indicator_state import IndicatorState  # noqa: E402
from backtest import V1Detector, V1DetectorConfig, Backtester  # noqa: E402

TICK = 0.25
POINT_VALUE = 2.00  # MNQ $2/point


# ─────────────────────────────────────────────────────────────────────────────
# BAR CONVERSION & AGGREGATION
# ─────────────────────────────────────────────────────────────────────────────

def _mnq_to_v3(bar: MnqBar) -> V3Bar:
    """Convert mnq.core.types.Bar (Decimal fields, .ts datetime) → V3 Bar."""
    return V3Bar(
        time=int(bar.ts.timestamp()),
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
    )


def _aggregate_1m_to_5m(bars_1m: list[V3Bar]) -> list[V3Bar]:
    """Aggregate 1-minute bars into 5-minute bars.

    Groups by 5-minute time boundary (floor to nearest 5min epoch).
    Returns bars sorted by time.
    """
    if not bars_1m:
        return []

    buckets: dict[int, list[V3Bar]] = {}
    for b in bars_1m:
        key = (b.time // 300) * 300  # Floor to 5min boundary
        buckets.setdefault(key, []).append(b)

    bars_5m: list[V3Bar] = []
    for key in sorted(buckets.keys()):
        group = buckets[key]
        bars_5m.append(V3Bar(
            time=key,
            open=group[0].open,
            high=max(b.high for b in group),
            low=min(b.low for b in group),
            close=group[-1].close,
            volume=sum(b.volume for b in group),
        ))
    return bars_5m


def _scrub_v3_day(bars: list[V3Bar]) -> list[V3Bar]:
    """Remove settlement artifacts and garbage bars from V3 bar list."""
    if not bars:
        return bars
    clean: list[V3Bar] = [bars[0]] if bars[0].close > 0 else []
    for b in bars[1:]:
        if b.close <= 0:
            continue
        if clean:
            prev_c = clean[-1].close
            if prev_c > 0 and abs(b.close - prev_c) / prev_c > 0.03:
                continue
        clean.append(b)
    return clean


# ─────────────────────────────────────────────────────────────────────────────
# TRADE RESOLUTION ON 1M BARS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class V3Trade:
    day_date: str
    setup: str
    side: str
    entry_bar_5m_ix: int
    entry_price: float
    stop: float
    tp1: float
    tp2: float
    sl_dist: float
    pm_final: float
    quant_total: float
    red_team: float
    regime: str
    voice_agree: int
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_r: float = 0.0
    pnl_dollars: float = 0.0
    bars_held_5m: int = 0
    mfe_r: float = 0.0
    mae_r: float = 0.0


@dataclass
class V3VariantStats:
    name: str
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    scratches: int = 0
    total_pnl_r: float = 0.0
    total_pnl_dollars: float = 0.0
    max_drawdown_r: float = 0.0
    peak_equity_r: float = 0.0
    equity_r: float = 0.0
    daily_pnls: list[float] = field(default_factory=list)
    trades: list[V3Trade] = field(default_factory=list)
    by_setup: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)
    days_traded: int = 0
    total_days: int = 0
    decisions_total: int = 0
    decisions_fired: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# RESOLVE EXIT ON 1M BARS FOR TICK-PRECISION
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_v3_exit_on_1m(
    *,
    side: str,
    entry_price: float,
    stop: float,
    tp1: float,
    tp2: float,
    sl_dist: float,
    use_partials: bool,
    use_mfe_trail: bool,
    trail_arm_R: float,
    trail_lock_R: float,
    bars_1m: list[V3Bar],
    signal_1m_ix: int,
    timeout_bars_5m: int,
) -> tuple[float, str, float, int, float, float]:
    """Walk 1m bars from signal point to find exit.

    Returns: (exit_price, exit_reason, pnl_r, bars_held_5m_equiv, mfe_r, mae_r)
    """
    timeout_bars_1m = timeout_bars_5m * 5  # Convert 5m timeout to 1m bars
    current_sl = stop
    tp1_filled = False
    mfe_r = 0.0
    mae_r = 0.0

    for i in range(signal_1m_ix + 1, min(signal_1m_ix + timeout_bars_1m + 1, len(bars_1m))):
        bar = bars_1m[i]
        held = i - signal_1m_ix

        # Track MAE/MFE
        if sl_dist > 0:
            if side == "long":
                fav = (bar.high - entry_price) / sl_dist
                adv = (bar.low - entry_price) / sl_dist
            else:
                fav = (entry_price - bar.low) / sl_dist
                adv = (entry_price - bar.high) / sl_dist
            mfe_r = max(mfe_r, fav)
            mae_r = min(mae_r, adv)

        # MFE-aware trailing
        if use_mfe_trail and mfe_r >= trail_arm_R and not tp1_filled and sl_dist > 0:
            lock_price = entry_price + (sl_dist * trail_lock_R) if side == "long" \
                else entry_price - (sl_dist * trail_lock_R)
            if side == "long":
                current_sl = max(current_sl, lock_price)
            else:
                current_sl = min(current_sl, lock_price)

        # Check stop
        if side == "long":
            sl_hit = bar.low <= current_sl
            tp1_hit = bar.high >= tp1
            tp2_hit = bar.high >= tp2
        else:
            sl_hit = bar.high >= current_sl
            tp1_hit = bar.low <= tp1
            tp2_hit = bar.low <= tp2

        # Partials logic
        if use_partials and not tp1_filled and tp1_hit and not sl_hit:
            tp1_filled = True
            current_sl = entry_price  # BE stop
            continue

        bars_5m_equiv = held // 5

        if sl_hit:
            if side == "long":
                actual_r = (current_sl - entry_price) / sl_dist
            else:
                actual_r = (entry_price - current_sl) / sl_dist
            if tp1_filled:
                pnl_r = max(actual_r, 0.5)
                reason = "tp1_then_be"
            elif actual_r > 0:
                pnl_r = actual_r
                reason = "trail_lock"
            else:
                pnl_r = actual_r
                reason = "stop"
            return current_sl, reason, pnl_r, bars_5m_equiv, mfe_r, mae_r

        if tp2_hit:
            if side == "long":
                actual_r = (tp2 - entry_price) / sl_dist
            else:
                actual_r = (entry_price - tp2) / sl_dist
            if use_partials and tp1_filled:
                pnl_r = actual_r * 0.5 + 0.5  # 50% at TP1 + 50% at TP2
                reason = "tp2_partial"
            else:
                pnl_r = actual_r
                reason = "tp2"
            return tp2, reason, pnl_r, bars_5m_equiv, mfe_r, mae_r

        if not use_partials and tp1_hit:
            if side == "long":
                actual_r = (tp1 - entry_price) / sl_dist
            else:
                actual_r = (entry_price - tp1) / sl_dist
            return tp1, "tp1", actual_r, bars_5m_equiv, mfe_r, mae_r

        # Timeout
        if held >= timeout_bars_1m:
            pnl_r = 0.5 if tp1_filled else 0.0
            return bar.close, "timeout", pnl_r, bars_5m_equiv, mfe_r, mae_r

    # Session end
    last = bars_1m[-1] if bars_1m else bars_1m[signal_1m_ix]
    pnl_r = 0.5 if tp1_filled else 0.0
    held_5m = (len(bars_1m) - signal_1m_ix) // 5
    return last.close, "session_end", pnl_r, held_5m, mfe_r, mae_r


# ─────────────────────────────────────────────────────────────────────────────
# FIND 1M BAR INDEX CLOSEST TO 5M BAR TIME
# ─────────────────────────────────────────────────────────────────────────────

def _find_1m_ix_for_5m_time(bars_1m: list[V3Bar], target_time: int) -> int:
    """Binary search for the 1m bar at or just after the 5m bar's close time."""
    lo, hi = 0, len(bars_1m) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if bars_1m[mid].time < target_time:
            lo = mid + 1
        else:
            hi = mid
    return lo


# ─────────────────────────────────────────────────────────────────────────────
# V3 VARIANT CONFIGS — sweep the major knobs
# ─────────────────────────────────────────────────────────────────────────────

V3_VARIANTS: list[tuple[str, FirmConfig, V1DetectorConfig]] = []

# v3_0: Winning config from 5m walk-forward (fibonacci + partials + pullback)
V3_VARIANTS.append((
    "v3_0_fib_partial_pb",
    FirmConfig(pm_threshold=40.0, require_setup=True),
    V1DetectorConfig(
        exit_mode="fibonacci", use_partials=True, entry_mode="pullback",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))

# v3_1: Same but market entry (test pullback value)
V3_VARIANTS.append((
    "v3_1_fib_partial_mkt",
    FirmConfig(pm_threshold=40.0, require_setup=True),
    V1DetectorConfig(
        exit_mode="fibonacci", use_partials=True, entry_mode="market",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))

# v3_2: R-multiple exits (legacy mode, control)
V3_VARIANTS.append((
    "v3_2_rmult_partial_pb",
    FirmConfig(pm_threshold=40.0, require_setup=True),
    V1DetectorConfig(
        exit_mode="r_multiple", use_partials=True, entry_mode="pullback",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))

# v3_3: No partials (full-size TP2 exits)
V3_VARIANTS.append((
    "v3_3_fib_noptl_pb",
    FirmConfig(pm_threshold=40.0, require_setup=True),
    V1DetectorConfig(
        exit_mode="fibonacci", use_partials=False, entry_mode="pullback",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))

# v3_4: Low PM threshold (more trades, test signal quality)
V3_VARIANTS.append((
    "v3_4_fib_partial_pm30",
    FirmConfig(pm_threshold=30.0, require_setup=True),
    V1DetectorConfig(
        exit_mode="fibonacci", use_partials=True, entry_mode="pullback",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))

# v3_5: High PM threshold (fewer trades, higher quality)
V3_VARIANTS.append((
    "v3_5_fib_partial_pm50",
    FirmConfig(pm_threshold=50.0, require_setup=True),
    V1DetectorConfig(
        exit_mode="fibonacci", use_partials=True, entry_mode="pullback",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))

# v3_6: No setup required (Firm-only signals)
V3_VARIANTS.append((
    "v3_6_firm_only",
    FirmConfig(pm_threshold=40.0, require_setup=False),
    V1DetectorConfig(
        exit_mode="fibonacci", use_partials=True, entry_mode="pullback",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))

# v3_7: Full session (not just Power Hours)
V3_VARIANTS.append((
    "v3_7_fib_full_session",
    FirmConfig(pm_threshold=40.0, require_setup=True),
    V1DetectorConfig(
        exit_mode="fibonacci", use_partials=True, entry_mode="pullback",
        ema_tod_filter="Full Session", ema_dow_filter="All Days",
    ),
))

# v3_8: Hybrid exits (Alligator for EMA PB, Fibonacci for ORB/Sweep)
V3_VARIANTS.append((
    "v3_8_hybrid_exits",
    FirmConfig(pm_threshold=40.0, require_setup=True),
    V1DetectorConfig(
        exit_mode="hybrid", use_partials=True, entry_mode="pullback",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))

# v3_9: Aggressive red team (1.5x penalty)
V3_VARIANTS.append((
    "v3_9_strong_redteam",
    FirmConfig(pm_threshold=40.0, require_setup=True, redteam_weight=1.5),
    V1DetectorConfig(
        exit_mode="fibonacci", use_partials=True, entry_mode="pullback",
        ema_tod_filter="Power Hours", ema_dow_filter="All Days",
    ),
))


def _backtest_v3_day(
    name: str,
    firm_cfg: FirmConfig,
    det_cfg: V1DetectorConfig,
    bars_1m_v3: list[V3Bar],
    bars_5m: list[V3Bar],
    day_date: str,
) -> tuple[list[V3Trade], int, int]:
    """Run the full V3 pipeline on one day. Returns (trades, decisions, fired)."""
    if not bars_5m or len(bars_5m) < 10:
        return [], 0, 0

    detector = V1Detector(cfg=det_cfg)
    state = IndicatorState()
    trades: list[V3Trade] = []
    decisions_total = 0
    decisions_fired = 0
    cooldown_until = -1

    for bar_ix, bar in enumerate(bars_5m):
        state.update(bar)
        if bar_ix < 3:  # Warm up indicators
            continue

        st = detector.detect(bar_ix, bar, state)
        atr_ma20 = state.atr_ma20()
        vol_z = state.vol_z()
        regime = detect_regime(bar.adx or 20, bar.atr or 0, atr_ma20, vol_z)

        d = evaluate(
            bar=bar, st=st, regime=regime,
            atr_ma20=atr_ma20, vol_z=vol_z,
            prev_adx_3=state.adx_3_bars_ago(),
            range_avg_20=state.range_avg_20(),
            vol_z_prev_1=state.vol_z_at(1),
            vol_z_prev_2=state.vol_z_at(2),
            highest_5_prev=state.highest_5_prev(),
            lowest_5_prev=state.lowest_5_prev(),
            recent_losses=0,  # simplified: no cross-day loss memory
            prev_day_high=state.prev_day_high,
            prev_day_low=state.prev_day_low,
            cfg=firm_cfg,
        )
        decisions_total += 1

        if not (d.fire_long or d.fire_short):
            continue
        if bar_ix < cooldown_until:
            continue

        decisions_fired += 1
        side = "long" if d.fire_long else "short"
        setup = d.setup_name or "FIRM"

        # Compute SL/TP using V3 Backtester logic
        use_fib = (det_cfg.exit_mode == "fibonacci" or
                   (det_cfg.exit_mode == "hybrid" and setup in ("ORB", "SWEEP")))

        entry_price = bar.close
        atr = bar.atr or 1.0

        if setup == "ORB":
            or_low = detector.or_low
            or_high = detector.or_high
            if side == "long" and or_low is not None:
                sl = or_low - atr * 0.15
            elif side == "short" and or_high is not None:
                sl = or_high + atr * 0.15
            else:
                sl = entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5

            if (use_fib and or_low is not None and or_high is not None
                    and (or_high - or_low) > atr * 0.5):
                or_range = or_high - or_low
                if side == "long":
                    tp1 = or_high + or_range * (det_cfg.fib_tp1_extension - 1.0)
                    tp2 = or_high + or_range * (det_cfg.fib_tp2_extension - 1.0)
                else:
                    tp1 = or_low - or_range * (det_cfg.fib_tp1_extension - 1.0)
                    tp2 = or_low - or_range * (det_cfg.fib_tp2_extension - 1.0)
            else:
                sl_dist = abs(entry_price - sl) or atr
                tp1 = entry_price + sl_dist * det_cfg.orb_tp1_r if side == "long" else entry_price - sl_dist * det_cfg.orb_tp1_r
                tp2 = entry_price + sl_dist * det_cfg.orb_tp2_r if side == "long" else entry_price - sl_dist * det_cfg.orb_tp2_r
            timeout = det_cfg.orb_timeout

        elif setup == "EMA PB":
            sl_dist_calc = atr * det_cfg.ema_sl_atr
            sl = entry_price - sl_dist_calc if side == "long" else entry_price + sl_dist_calc
            sl_dist = abs(entry_price - sl) or atr
            tp1 = entry_price + sl_dist * det_cfg.ema_tp1_r if side == "long" else entry_price - sl_dist * det_cfg.ema_tp1_r
            tp2 = entry_price + sl_dist * det_cfg.ema_tp2_r if side == "long" else entry_price - sl_dist * det_cfg.ema_tp2_r
            timeout = det_cfg.ema_timeout

        elif setup == "SWEEP":
            buf = det_cfg.sweep_sl_ticks * TICK
            swept_lo = detector.swept_lo_px
            swept_hi = detector.swept_hi_px
            if side == "long" and swept_lo is not None:
                sl = swept_lo - buf
            elif side == "short" and swept_hi is not None:
                sl = swept_hi + buf
            else:
                sl = entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5

            if (use_fib and swept_lo is not None and swept_hi is not None
                    and abs(swept_hi - swept_lo) > atr * 0.5):
                swept_range = min(abs(swept_hi - swept_lo), atr * 3.0)
                if side == "long":
                    tp1 = entry_price + swept_range * det_cfg.fib_tp1_extension
                    tp2 = entry_price + swept_range * det_cfg.fib_tp2_extension
                else:
                    tp1 = entry_price - swept_range * det_cfg.fib_tp1_extension
                    tp2 = entry_price - swept_range * det_cfg.fib_tp2_extension
            else:
                sl_dist = abs(entry_price - sl) or atr
                tp1 = entry_price + sl_dist * det_cfg.sweep_tp1_r if side == "long" else entry_price - sl_dist * det_cfg.sweep_tp1_r
                tp2 = entry_price + sl_dist * det_cfg.sweep_tp2_r if side == "long" else entry_price - sl_dist * det_cfg.sweep_tp2_r
            timeout = det_cfg.sweep_timeout
        else:
            sl = entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5
            sl_dist = abs(entry_price - sl) or atr
            tp1 = entry_price + sl_dist * 1.0 if side == "long" else entry_price - sl_dist * 1.0
            tp2 = entry_price + sl_dist * 2.0 if side == "long" else entry_price - sl_dist * 2.0
            timeout = 30

        sl_dist = abs(entry_price - sl)
        if sl_dist <= 0:
            continue

        # Resolve exit on 1m bars for tick-level precision
        signal_1m_ix = _find_1m_ix_for_5m_time(bars_1m_v3, bar.time + 300)
        exit_px, exit_reason, pnl_r, bars_held_5m, mfe_r, mae_r = _resolve_v3_exit_on_1m(
            side=side,
            entry_price=entry_price,
            stop=sl,
            tp1=tp1,
            tp2=tp2,
            sl_dist=sl_dist,
            use_partials=det_cfg.use_partials,
            use_mfe_trail=det_cfg.use_mfe_trail,
            trail_arm_R=det_cfg.trail_arm_R,
            trail_lock_R=det_cfg.trail_lock_R,
            bars_1m=bars_1m_v3,
            signal_1m_ix=signal_1m_ix,
            timeout_bars_5m=timeout,
        )

        pnl_dollars = pnl_r * sl_dist * POINT_VALUE

        trades.append(V3Trade(
            day_date=day_date,
            setup=setup,
            side=side,
            entry_bar_5m_ix=bar_ix,
            entry_price=entry_price,
            stop=sl,
            tp1=tp1,
            tp2=tp2,
            sl_dist=sl_dist,
            pm_final=d.pm_final,
            quant_total=d.quant_total,
            red_team=d.red_team,
            regime=regime,
            voice_agree=d.voice_agree,
            exit_price=exit_px,
            exit_reason=exit_reason,
            pnl_r=pnl_r,
            pnl_dollars=pnl_dollars,
            bars_held_5m=bars_held_5m,
            mfe_r=mfe_r,
            mae_r=mae_r,
        ))

        # Cooldown: skip next N 5m bars after a trade
        cooldown_until = bar_ix + det_cfg.cooldown

    return trades, decisions_total, decisions_fired


def _compute_v3_stats(name: str, trades: list[V3Trade], total_days: int) -> V3VariantStats:
    vs = V3VariantStats(name=name, total_days=total_days)
    vs.total_trades = len(trades)
    vs.trades = trades

    day_trades: dict[str, list[V3Trade]] = {}
    for t in trades:
        day_trades.setdefault(t.day_date, []).append(t)
    vs.days_traded = len(day_trades)

    for t in trades:
        vs.total_pnl_r += t.pnl_r
        vs.total_pnl_dollars += t.pnl_dollars
        vs.equity_r += t.pnl_r
        vs.peak_equity_r = max(vs.peak_equity_r, vs.equity_r)
        dd = vs.peak_equity_r - vs.equity_r
        vs.max_drawdown_r = max(vs.max_drawdown_r, dd)

        if t.pnl_r > 0:
            vs.winners += 1
        elif t.pnl_r < 0:
            vs.losers += 1
        else:
            vs.scratches += 1

        # By setup
        vs.by_setup.setdefault(t.setup, {"trades": 0, "wins": 0, "total_r": 0.0})
        vs.by_setup[t.setup]["trades"] += 1
        vs.by_setup[t.setup]["total_r"] += t.pnl_r
        if t.pnl_r > 0:
            vs.by_setup[t.setup]["wins"] += 1

        # By regime
        vs.by_regime.setdefault(t.regime, {"trades": 0, "wins": 0, "total_r": 0.0})
        vs.by_regime[t.regime]["trades"] += 1
        vs.by_regime[t.regime]["total_r"] += t.pnl_r
        if t.pnl_r > 0:
            vs.by_regime[t.regime]["wins"] += 1

    # Daily PnLs (for Sharpe)
    all_dates = sorted(day_trades.keys())
    for d in all_dates:
        day_pnl = sum(t.pnl_dollars for t in day_trades.get(d, []))
        vs.daily_pnls.append(day_pnl)

    return vs


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch 13 — V3 real-tape backtest")
    parser.add_argument("--max-days", type=int, default=0, help="Limit to last N days (0=all)")
    parser.add_argument("--variants", nargs="*", help="Variant names to test (default: all)")
    parser.add_argument("--pm", type=float, default=None, help="Override PM threshold for all variants")
    parser.add_argument("--no-partials", action="store_true", help="Disable partials for all variants")
    parser.add_argument("--exit-mode", default=None, choices=["r_multiple", "fibonacci", "hybrid"])
    args = parser.parse_args()

    print("backtest_real_v3: loading Databento 1m tape...")
    t0 = time.monotonic()
    days = load_databento_days(days_tail=args.max_days or None)
    load_s = time.monotonic() - t0
    print(f"  loaded {len(days)} RTH days in {load_s:.1f}s")
    if not days:
        print("  ERROR: no days loaded — check data path")
        sys.exit(1)

    first_date = days[0][0].ts.date()
    last_date = days[-1][0].ts.date()
    print(f"  date range: {first_date} → {last_date}")

    # Convert all days: mnq Bar → V3 Bar, then aggregate to 5m
    print("  converting 1m → V3 format + aggregating to 5m...")
    t1 = time.monotonic()
    day_data: list[tuple[str, list[V3Bar], list[V3Bar]]] = []  # (date, 1m_bars, 5m_bars)
    dropped_days = 0

    for day_bars in days:
        day_date = day_bars[0].ts.strftime("%Y-%m-%d")
        bars_1m_v3 = [_mnq_to_v3(b) for b in day_bars]
        bars_1m_v3 = _scrub_v3_day(bars_1m_v3)
        if len(bars_1m_v3) < 350:
            dropped_days += 1
            continue
        bars_5m = _aggregate_1m_to_5m(bars_1m_v3)
        if len(bars_5m) < 60:  # Need ~75 5m bars for meaningful analysis
            dropped_days += 1
            continue
        day_data.append((day_date, bars_1m_v3, bars_5m))

    convert_s = time.monotonic() - t1
    print(f"  {len(day_data)} clean days ({dropped_days} dropped) in {convert_s:.1f}s")

    # Select variants
    variants = V3_VARIANTS
    if args.variants:
        variants = [(n, fc, dc) for n, fc, dc in V3_VARIANTS if n in args.variants]
        if not variants:
            print(f"  ERROR: no matching variants. Available: {[v[0] for v in V3_VARIANTS]}")
            sys.exit(1)

    # Apply overrides
    if args.pm is not None or args.no_partials or args.exit_mode:
        new_variants = []
        for name, fc, dc in variants:
            if args.pm is not None:
                fc = FirmConfig(pm_threshold=args.pm, require_setup=fc.require_setup,
                               redteam_weight=fc.redteam_weight)
            if args.no_partials:
                dc = V1DetectorConfig(**{**dc.__dict__, "use_partials": False})
            if args.exit_mode:
                dc = V1DetectorConfig(**{**dc.__dict__, "exit_mode": args.exit_mode})
            new_variants.append((name, fc, dc))
        variants = new_variants

    all_stats: list[V3VariantStats] = []
    daily_pnl_data: dict[str, dict[str, float]] = {}

    for vi, (name, firm_cfg, det_cfg) in enumerate(variants):
        print(f"\n  [{vi + 1}/{len(variants)}] {name}...", flush=True)
        t2 = time.monotonic()
        all_trades: list[V3Trade] = []
        total_dec = 0
        total_fired = 0

        for day_date, bars_1m_v3, bars_5m in day_data:
            day_trades, dec, fired = _backtest_v3_day(
                name, firm_cfg, det_cfg, bars_1m_v3, bars_5m, day_date,
            )
            all_trades.extend(day_trades)
            total_dec += dec
            total_fired += fired

        vs = _compute_v3_stats(name, all_trades, total_days=len(day_data))
        vs.decisions_total = total_dec
        vs.decisions_fired = total_fired
        all_stats.append(vs)

        # Daily PnL map
        day_pnl_map: dict[str, float] = {}
        for day_date, _, _ in day_data:
            day_pnl_map[day_date] = 0.0
        for t in all_trades:
            day_pnl_map[t.day_date] = day_pnl_map.get(t.day_date, 0.0) + t.pnl_dollars
        daily_pnl_data[name] = day_pnl_map

        elapsed = time.monotonic() - t2
        wr = vs.winners / vs.total_trades * 100 if vs.total_trades else 0
        pf = (sum(t.pnl_r for t in all_trades if t.pnl_r > 0) /
              abs(sum(t.pnl_r for t in all_trades if t.pnl_r < 0))
              if any(t.pnl_r < 0 for t in all_trades) else float('inf'))
        print(f"    {vs.total_trades} trades ({total_fired} signals), "
              f"R={vs.total_pnl_r:+.2f}, ${vs.total_pnl_dollars:+.2f}, "
              f"WR {wr:.0f}%, PF {pf:.2f}, MDD {vs.max_drawdown_r:.2f}R, {elapsed:.1f}s")

        # Setup breakdown
        for setup, st in vs.by_setup.items():
            setup_wr = st["wins"] / st["trades"] * 100 if st["trades"] else 0
            print(f"      {setup:8s}: {st['trades']:3d} trades  {setup_wr:5.1f}% win  {st['total_r']:+.2f}R")

    # ─── Write Reports ───
    report_dir = REPO_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)

    lines = [
        f"# Batch 13 — V3 Real-Tape Backtest — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"Apex V3 15-voice engine on Databento MNQ 1m tape, aggregated to 5m for detection.",
        f"**{len(day_data)} clean RTH days** ({first_date} → {last_date})",
        "",
        "Zero slippage, zero commission. Exits resolved on 1m bars for tick-precision.",
        "Intermarket voices (V8-V11) return 0 — no sibling data in tape.",
        "",
        "## Variant Summary",
        "",
        "| Variant | Trades | Signals | W | L | WR% | Total R | Avg R | PF | MaxDD R | $ PnL | Sharpe |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for vs in sorted(all_stats, key=lambda x: x.total_pnl_r, reverse=True):
        wr = vs.winners / vs.total_trades * 100 if vs.total_trades else 0
        avg_r = vs.total_pnl_r / vs.total_trades if vs.total_trades else 0
        gw = sum(t.pnl_r for t in vs.trades if t.pnl_r > 0)
        gl = abs(sum(t.pnl_r for t in vs.trades if t.pnl_r < 0))
        pf = gw / gl if gl > 0 else float('inf')
        if len(vs.daily_pnls) > 1:
            mu = statistics.mean(vs.daily_pnls)
            sd = statistics.stdev(vs.daily_pnls)
            sharpe = (mu / sd * (252 ** 0.5)) if sd > 0 else 0.0
        else:
            sharpe = 0.0
        pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
        lines.append(
            f"| {vs.name} | {vs.total_trades} | {vs.decisions_fired} | {vs.winners} | {vs.losers} "
            f"| {wr:.1f} | {vs.total_pnl_r:+.2f} | {avg_r:+.3f} | {pf_str} "
            f"| {vs.max_drawdown_r:.2f} | ${vs.total_pnl_dollars:+,.2f} | {sharpe:+.2f} |"
        )

    # Deep dive on top 3
    top3 = sorted(all_stats, key=lambda x: x.total_pnl_r, reverse=True)[:3]
    for vs in top3:
        lines.append("")
        lines.append(f"### {vs.name}")
        lines.append("")
        lines.append(f"- **Trades:** {vs.total_trades} (of {vs.decisions_fired} signals fired)")
        lines.append(f"- **Days traded:** {vs.days_traded} / {vs.total_days}")
        if vs.total_trades:
            avg_mfe = statistics.mean(t.mfe_r for t in vs.trades)
            avg_mae = statistics.mean(t.mae_r for t in vs.trades)
            lines.append(f"- **Avg MFE:** {avg_mfe:+.2f}R  |  Avg MAE: {avg_mae:+.2f}R")
        lines.append("")
        lines.append("  **By Setup:**")
        for setup, st in sorted(vs.by_setup.items()):
            setup_wr = st["wins"] / st["trades"] * 100 if st["trades"] else 0
            lines.append(f"  - {setup}: {st['trades']} trades, {setup_wr:.0f}% WR, {st['total_r']:+.2f}R")
        lines.append("")
        lines.append("  **By Regime:**")
        for regime, rs in sorted(vs.by_regime.items()):
            reg_wr = rs["wins"] / rs["trades"] * 100 if rs["trades"] else 0
            lines.append(f"  - {regime}: {rs['trades']} trades, {reg_wr:.0f}% WR, {rs['total_r']:+.2f}R")

    # Key finding
    best = top3[0] if top3 else None
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    if best and best.total_trades > 0:
        wr = best.winners / best.total_trades * 100
        avg_r = best.total_pnl_r / best.total_trades
        if best.total_pnl_r > 0 and wr > 50:
            lines.append(f"**V3 ENGINE HAS EDGE** — Best: {best.name} with {best.total_trades} trades, "
                        f"{wr:.0f}% WR, {best.total_pnl_r:+.2f}R total, "
                        f"${best.total_pnl_dollars:+,.2f} over {best.total_days} days.")
        elif best.total_pnl_r > 0:
            lines.append(f"**V3 ENGINE MARGINAL** — Best: {best.name} with {best.total_trades} trades, "
                        f"{wr:.0f}% WR, {best.total_pnl_r:+.2f}R. Positive but low WR — investigate.")
        else:
            lines.append(f"**V3 ENGINE NO EDGE** — All variants net negative on real tape. "
                        f"Best: {best.name} at {best.total_pnl_r:+.2f}R.")
    else:
        lines.append("**NO TRADES** — V3 engine produced no signals on the real tape. "
                     "Check PM threshold and setup detection parameters.")

    lines.append("")
    lines.append(f"*Generated in {time.monotonic() - t0:.1f}s*")

    (report_dir / "backtest_real_v3.md").write_text("\n".join(lines))
    print(f"\nWrote reports/backtest_real_v3.md")

    # Trade log CSV
    csv_lines = [
        "variant,date,setup,side,entry_5m_ix,entry_px,stop,tp1,tp2,sl_dist,"
        "pm_final,quant,red_team,regime,voice_agree,"
        "exit_px,exit_reason,pnl_r,pnl_dollars,bars_5m,mfe_r,mae_r"
    ]
    for vs in all_stats:
        for t in vs.trades:
            csv_lines.append(
                f"{vs.name},{t.day_date},{t.setup},{t.side},{t.entry_bar_5m_ix},"
                f"{t.entry_price:.2f},{t.stop:.2f},{t.tp1:.2f},{t.tp2:.2f},{t.sl_dist:.2f},"
                f"{t.pm_final:.1f},{t.quant_total:.1f},{t.red_team:.1f},{t.regime},{t.voice_agree},"
                f"{t.exit_price:.2f},{t.exit_reason},{t.pnl_r:.3f},{t.pnl_dollars:.2f},"
                f"{t.bars_held_5m},{t.mfe_r:.2f},{t.mae_r:.2f}"
            )
    (report_dir / "backtest_real_v3_trades.csv").write_text("\n".join(csv_lines))
    print(f"Wrote reports/backtest_real_v3_trades.csv ({len(csv_lines) - 1} trades)")

    # Daily PnL JSON
    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "backtest_real_v3_daily.json").write_text(json.dumps(daily_pnl_data, indent=2))
    print("Wrote data/backtest_real_v3_daily.json")


if __name__ == "__main__":
    main()
