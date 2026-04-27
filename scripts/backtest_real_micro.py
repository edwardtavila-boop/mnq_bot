"""Batch 14 — Microstructure entry refinement on real Databento MNQ tape.

Takes the V3 pipeline from Batch 13 and adds the MicroEntryRefiner layer:
when the 5m V3 engine fires a signal, the MicroEntryRefiner examines the
next 5 × 1m bars for a confirmation pattern (ORB confirm, EMA pin bar,
Sweep retest). If confirmed, the trade enters at the micro-refined price
with a tighter stop — improving R:R. If no micro confirmation, the trade
is either skipped entirely or falls back to the original signal.

Variants tested:
  - micro_strict: Skip trade if no micro confirm (pure micro-gating)
  - micro_fallback: Fall back to 5m signal if no micro confirm
  - v3_baseline: No micro (Batch 13 control, for direct comparison)

Output:
    reports/backtest_real_micro.md          — variant summary + micro stats
    reports/backtest_real_micro_trades.csv  — full trade log
    data/backtest_real_micro_daily.json     — per-day PnL

Usage:
    python scripts/backtest_real_micro.py
    python scripts/backtest_real_micro.py --max-days 200
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

from backtest import V1Detector, V1DetectorConfig  # noqa: E402
from firm_engine import (  # noqa: E402
    Bar as V3Bar,
)
from firm_engine import (
    FirmConfig,
    detect_regime,
    evaluate,
)
from indicator_state import IndicatorState  # noqa: E402
from microstructure import MicroBar, MicroEntryRefiner  # noqa: E402
from real_bars import load_databento_days  # noqa: E402

from mnq.core.types import Bar as MnqBar  # noqa: E402

TICK = 0.25
POINT_VALUE = 2.00


def _mnq_to_v3(bar: MnqBar) -> V3Bar:
    return V3Bar(
        time=int(bar.ts.timestamp()),
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
    )


def _v3_to_micro(bar: V3Bar) -> MicroBar:
    return MicroBar(
        time=bar.time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
    )


def _aggregate_1m_to_5m(bars_1m: list[V3Bar]) -> list[V3Bar]:
    if not bars_1m:
        return []
    buckets: dict[int, list[V3Bar]] = {}
    for b in bars_1m:
        key = (b.time // 300) * 300
        buckets.setdefault(key, []).append(b)
    bars_5m: list[V3Bar] = []
    for key in sorted(buckets.keys()):
        group = buckets[key]
        bars_5m.append(
            V3Bar(
                time=key,
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=sum(b.volume for b in group),
            )
        )
    return bars_5m


def _scrub_v3_day(bars: list[V3Bar]) -> list[V3Bar]:
    if not bars:
        return bars
    clean: list[V3Bar] = [bars[0]] if bars[0].close > 0 else []
    for b in bars[1:]:
        if b.close <= 0:
            continue
        if (
            clean
            and clean[-1].close > 0
            and abs(b.close - clean[-1].close) / clean[-1].close > 0.03
        ):
            continue
        clean.append(b)
    return clean


def _find_1m_ix(bars_1m: list[V3Bar], target_time: int) -> int:
    lo, hi = 0, len(bars_1m) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if bars_1m[mid].time < target_time:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _get_micro_bars(bars_1m_v3: list[V3Bar], start_time: int, n: int = 5) -> list[MicroBar]:
    """Get n MicroBar objects starting at start_time from V3 bars."""
    ix = _find_1m_ix(bars_1m_v3, start_time)
    result = []
    for i in range(ix, min(ix + n, len(bars_1m_v3))):
        result.append(_v3_to_micro(bars_1m_v3[i]))
    return result


def _resolve_exit_on_1m(
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
    timeout_bars_1m: int,
) -> tuple[float, str, float, int, float, float]:
    """Walk 1m bars from entry to find exit. Returns (exit_px, reason, pnl_r, bars_5m, mfe_r, mae_r)."""
    current_sl = stop
    tp1_filled = False
    mfe_r = mae_r = 0.0

    for i in range(signal_1m_ix + 1, min(signal_1m_ix + timeout_bars_1m + 1, len(bars_1m))):
        bar = bars_1m[i]
        held = i - signal_1m_ix

        if sl_dist > 0:
            if side == "long":
                fav = (bar.high - entry_price) / sl_dist
                adv = (bar.low - entry_price) / sl_dist
            else:
                fav = (entry_price - bar.low) / sl_dist
                adv = (entry_price - bar.high) / sl_dist
            mfe_r = max(mfe_r, fav)
            mae_r = min(mae_r, adv)

        if use_mfe_trail and mfe_r >= trail_arm_R and not tp1_filled and sl_dist > 0:
            lock = (
                entry_price + (sl_dist * trail_lock_R)
                if side == "long"
                else entry_price - (sl_dist * trail_lock_R)
            )
            current_sl = max(current_sl, lock) if side == "long" else min(current_sl, lock)

        if side == "long":
            sl_hit, tp1_hit, tp2_hit = bar.low <= current_sl, bar.high >= tp1, bar.high >= tp2
        else:
            sl_hit, tp1_hit, tp2_hit = bar.high >= current_sl, bar.low <= tp1, bar.low <= tp2

        if use_partials and not tp1_filled and tp1_hit and not sl_hit:
            tp1_filled = True
            current_sl = entry_price
            continue

        bars_5m = held // 5

        if sl_hit:
            actual_r = (
                (current_sl - entry_price) / sl_dist
                if side == "long"
                else (entry_price - current_sl) / sl_dist
            )
            if tp1_filled:
                return current_sl, "tp1_then_be", max(actual_r, 0.5), bars_5m, mfe_r, mae_r
            if actual_r > 0:
                return current_sl, "trail_lock", actual_r, bars_5m, mfe_r, mae_r
            return current_sl, "stop", actual_r, bars_5m, mfe_r, mae_r

        if tp2_hit:
            actual_r = (
                (tp2 - entry_price) / sl_dist if side == "long" else (entry_price - tp2) / sl_dist
            )
            if use_partials and tp1_filled:
                return tp2, "tp2_partial", actual_r * 0.5 + 0.5, bars_5m, mfe_r, mae_r
            return tp2, "tp2", actual_r, bars_5m, mfe_r, mae_r

        if not use_partials and tp1_hit:
            actual_r = (
                (tp1 - entry_price) / sl_dist if side == "long" else (entry_price - tp1) / sl_dist
            )
            return tp1, "tp1", actual_r, bars_5m, mfe_r, mae_r

        if held >= timeout_bars_1m:
            return bar.close, "timeout", (0.5 if tp1_filled else 0.0), bars_5m, mfe_r, mae_r

    last = bars_1m[-1] if bars_1m else bars_1m[signal_1m_ix]
    return (
        last.close,
        "session_end",
        (0.5 if tp1_filled else 0.0),
        (len(bars_1m) - signal_1m_ix) // 5,
        mfe_r,
        mae_r,
    )


@dataclass
class MicroTrade:
    day_date: str
    setup: str
    side: str
    micro_mode: str  # "strict", "fallback", "baseline"
    micro_confirmed: bool
    micro_reason: str
    micro_confidence: float
    micro_bars_waited: int
    micro_refined_r: float
    entry_price: float
    stop: float
    tp1: float
    tp2: float
    sl_dist: float
    pm_final: float
    regime: str
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_r: float = 0.0
    pnl_dollars: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0


@dataclass
class MicroStats:
    name: str
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    total_pnl_r: float = 0.0
    total_pnl_dollars: float = 0.0
    max_drawdown_r: float = 0.0
    peak_equity_r: float = 0.0
    equity_r: float = 0.0
    daily_pnls: list[float] = field(default_factory=list)
    trades: list[MicroTrade] = field(default_factory=list)
    by_setup: dict = field(default_factory=dict)
    micro_confirmed_count: int = 0
    micro_skipped_count: int = 0
    micro_avg_refined_r: float = 0.0
    days_traded: int = 0
    total_days: int = 0


def _compute_sl_tp(setup, side, det_cfg, detector, bar, entry_price):
    """Compute SL/TP for a given setup (mirrors V3 logic)."""
    atr = bar.atr or 1.0
    use_fib = det_cfg.exit_mode == "fibonacci" or (
        det_cfg.exit_mode == "hybrid" and setup in ("ORB", "SWEEP")
    )

    if setup == "ORB":
        or_low, or_high = detector.or_low, detector.or_high
        if side == "long" and or_low is not None:
            sl = or_low - atr * 0.15
        elif side == "short" and or_high is not None:
            sl = or_high + atr * 0.15
        else:
            sl = entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5

        if (
            use_fib
            and or_low is not None
            and or_high is not None
            and (or_high - or_low) > atr * 0.5
        ):
            or_range = or_high - or_low
            if side == "long":
                tp1 = or_high + or_range * (det_cfg.fib_tp1_extension - 1.0)
                tp2 = or_high + or_range * (det_cfg.fib_tp2_extension - 1.0)
            else:
                tp1 = or_low - or_range * (det_cfg.fib_tp1_extension - 1.0)
                tp2 = or_low - or_range * (det_cfg.fib_tp2_extension - 1.0)
        else:
            sd = abs(entry_price - sl) or atr
            tp1 = (
                entry_price + sd * det_cfg.orb_tp1_r
                if side == "long"
                else entry_price - sd * det_cfg.orb_tp1_r
            )
            tp2 = (
                entry_price + sd * det_cfg.orb_tp2_r
                if side == "long"
                else entry_price - sd * det_cfg.orb_tp2_r
            )
        timeout = det_cfg.orb_timeout

    elif setup == "EMA PB":
        sl_dist_calc = atr * det_cfg.ema_sl_atr
        sl = entry_price - sl_dist_calc if side == "long" else entry_price + sl_dist_calc
        sd = abs(entry_price - sl) or atr
        tp1 = (
            entry_price + sd * det_cfg.ema_tp1_r
            if side == "long"
            else entry_price - sd * det_cfg.ema_tp1_r
        )
        tp2 = (
            entry_price + sd * det_cfg.ema_tp2_r
            if side == "long"
            else entry_price - sd * det_cfg.ema_tp2_r
        )
        timeout = det_cfg.ema_timeout

    elif setup == "SWEEP":
        buf = det_cfg.sweep_sl_ticks * TICK
        swept_lo, swept_hi = detector.swept_lo_px, detector.swept_hi_px
        if side == "long" and swept_lo is not None:
            sl = swept_lo - buf
        elif side == "short" and swept_hi is not None:
            sl = swept_hi + buf
        else:
            sl = entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5

        if (
            use_fib
            and swept_lo is not None
            and swept_hi is not None
            and abs(swept_hi - swept_lo) > atr * 0.5
        ):
            swept_range = min(abs(swept_hi - swept_lo), atr * 3.0)
            if side == "long":
                tp1 = entry_price + swept_range * det_cfg.fib_tp1_extension
                tp2 = entry_price + swept_range * det_cfg.fib_tp2_extension
            else:
                tp1 = entry_price - swept_range * det_cfg.fib_tp1_extension
                tp2 = entry_price - swept_range * det_cfg.fib_tp2_extension
        else:
            sd = abs(entry_price - sl) or atr
            tp1 = (
                entry_price + sd * det_cfg.sweep_tp1_r
                if side == "long"
                else entry_price - sd * det_cfg.sweep_tp1_r
            )
            tp2 = (
                entry_price + sd * det_cfg.sweep_tp2_r
                if side == "long"
                else entry_price - sd * det_cfg.sweep_tp2_r
            )
        timeout = det_cfg.sweep_timeout
    else:
        sl = entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5
        sd = abs(entry_price - sl) or atr
        tp1 = entry_price + sd if side == "long" else entry_price - sd
        tp2 = entry_price + sd * 2 if side == "long" else entry_price - sd * 2
        timeout = 30

    return sl, tp1, tp2, timeout


def _backtest_micro_day(
    micro_mode: str,  # "strict", "fallback", "baseline"
    firm_cfg: FirmConfig,
    det_cfg: V1DetectorConfig,
    refiner: MicroEntryRefiner,
    bars_1m_v3: list[V3Bar],
    bars_5m: list[V3Bar],
    day_date: str,
) -> list[MicroTrade]:
    if not bars_5m or len(bars_5m) < 10:
        return []

    detector = V1Detector(cfg=det_cfg)
    state = IndicatorState()
    trades: list[MicroTrade] = []
    cooldown_until = -1

    for bar_ix, bar in enumerate(bars_5m):
        state.update(bar)
        if bar_ix < 3:
            continue

        st = detector.detect(bar_ix, bar, state)
        atr_ma20 = state.atr_ma20()
        vol_z = state.vol_z()
        regime = detect_regime(bar.adx or 20, bar.atr or 0, atr_ma20, vol_z)

        d = evaluate(
            bar=bar,
            st=st,
            regime=regime,
            atr_ma20=atr_ma20,
            vol_z=vol_z,
            prev_adx_3=state.adx_3_bars_ago(),
            range_avg_20=state.range_avg_20(),
            vol_z_prev_1=state.vol_z_at(1),
            vol_z_prev_2=state.vol_z_at(2),
            highest_5_prev=state.highest_5_prev(),
            lowest_5_prev=state.lowest_5_prev(),
            recent_losses=0,
            prev_day_high=state.prev_day_high,
            prev_day_low=state.prev_day_low,
            cfg=firm_cfg,
        )

        if not (d.fire_long or d.fire_short):
            continue
        if bar_ix < cooldown_until:
            continue

        side = "long" if d.fire_long else "short"
        setup = d.setup_name or "FIRM"
        atr = bar.atr or 1.0

        # Compute original SL/TP from 5m signal
        sl, tp1, tp2, timeout = _compute_sl_tp(setup, side, det_cfg, detector, bar, bar.close)
        sl_dist = abs(bar.close - sl)
        if sl_dist <= 0:
            continue

        # Get 1m micro bars starting after this 5m bar
        micro_bars = _get_micro_bars(bars_1m_v3, bar.time + 300, n=5)

        # Apply microstructure refinement
        micro_confirmed = False
        micro_reason = ""
        micro_confidence = 0.0
        micro_bars_waited = 0
        micro_refined_r = 1.0
        entry_price = bar.close
        final_sl = sl

        if micro_mode != "baseline" and micro_bars:
            if setup == "ORB":
                or_h = detector.or_high or bar.close
                or_l = detector.or_low or bar.close
                micro = refiner.refine_orb(side, bar.close, sl, or_h, or_l, micro_bars)
            elif setup == "EMA PB":
                micro = refiner.refine_ema_pullback(
                    side,
                    bar.close,
                    sl,
                    bar.ema9 or bar.close,
                    bar.ema21 or bar.close,
                    atr,
                    micro_bars,
                )
            elif setup == "SWEEP":
                swept = detector.swept_lo_px if side == "long" else detector.swept_hi_px
                if swept is None:
                    swept = bar.close
                micro = refiner.refine_sweep(side, bar.close, sl, swept, micro_bars)
            else:
                micro = None

            if micro is not None:
                micro_reason = micro.reason
                micro_confidence = micro.confidence
                micro_bars_waited = micro.bars_waited
                micro_refined_r = micro.refined_r_mult

                if micro.entered:
                    micro_confirmed = True
                    entry_price = micro.entry_price
                    final_sl = micro.micro_sl
                    # Recompute sl_dist and TPs for refined entry
                    sl_dist = abs(entry_price - final_sl)
                    if sl_dist <= 0:
                        continue
                    # Recompute TPs relative to new entry
                    _, tp1, tp2, _ = _compute_sl_tp(
                        setup, side, det_cfg, detector, bar, entry_price
                    )
                else:
                    # No micro confirmation
                    if micro_mode == "strict":
                        # Skip the trade entirely
                        cooldown_until = bar_ix + det_cfg.cooldown
                        continue
                    # fallback: take the original 5m signal
                    micro_reason = f"fallback: {micro.reason}"
        elif micro_mode == "baseline":
            micro_reason = "baseline_no_micro"

        # Resolve exit on 1m bars
        signal_1m_ix = _find_1m_ix(bars_1m_v3, bar.time + 300)
        if micro_confirmed:
            # Start exit resolution from the micro entry bar
            signal_1m_ix = _find_1m_ix(bars_1m_v3, bar.time + 300 + micro_bars_waited * 60)

        exit_px, exit_reason, pnl_r, _, mfe_r, mae_r = _resolve_exit_on_1m(
            side=side,
            entry_price=entry_price,
            stop=final_sl,
            tp1=tp1,
            tp2=tp2,
            sl_dist=sl_dist,
            use_partials=det_cfg.use_partials,
            use_mfe_trail=det_cfg.use_mfe_trail,
            trail_arm_R=det_cfg.trail_arm_R,
            trail_lock_R=det_cfg.trail_lock_R,
            bars_1m=bars_1m_v3,
            signal_1m_ix=signal_1m_ix,
            timeout_bars_1m=timeout * 5,
        )

        pnl_dollars = pnl_r * sl_dist * POINT_VALUE

        trades.append(
            MicroTrade(
                day_date=day_date,
                setup=setup,
                side=side,
                micro_mode=micro_mode,
                micro_confirmed=micro_confirmed,
                micro_reason=micro_reason,
                micro_confidence=micro_confidence,
                micro_bars_waited=micro_bars_waited,
                micro_refined_r=micro_refined_r,
                entry_price=entry_price,
                stop=final_sl,
                tp1=tp1,
                tp2=tp2,
                sl_dist=sl_dist,
                pm_final=d.pm_final,
                regime=regime,
                exit_price=exit_px,
                exit_reason=exit_reason,
                pnl_r=pnl_r,
                pnl_dollars=pnl_dollars,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )
        )
        cooldown_until = bar_ix + det_cfg.cooldown

    return trades


def _compute_micro_stats(name: str, trades: list[MicroTrade], total_days: int) -> MicroStats:
    vs = MicroStats(name=name, total_days=total_days)
    vs.total_trades = len(trades)
    vs.trades = trades

    day_trades: dict[str, list[MicroTrade]] = {}
    for t in trades:
        day_trades.setdefault(t.day_date, []).append(t)
    vs.days_traded = len(day_trades)

    confirmed_rs = []
    for t in trades:
        vs.total_pnl_r += t.pnl_r
        vs.total_pnl_dollars += t.pnl_dollars
        vs.equity_r += t.pnl_r
        vs.peak_equity_r = max(vs.peak_equity_r, vs.equity_r)
        vs.max_drawdown_r = max(vs.max_drawdown_r, vs.peak_equity_r - vs.equity_r)

        if t.pnl_r > 0:
            vs.winners += 1
        elif t.pnl_r < 0:
            vs.losers += 1

        vs.by_setup.setdefault(t.setup, {"trades": 0, "wins": 0, "total_r": 0.0})
        vs.by_setup[t.setup]["trades"] += 1
        vs.by_setup[t.setup]["total_r"] += t.pnl_r
        if t.pnl_r > 0:
            vs.by_setup[t.setup]["wins"] += 1

        if t.micro_confirmed:
            vs.micro_confirmed_count += 1
            confirmed_rs.append(t.micro_refined_r)
        else:
            vs.micro_skipped_count += 1

    if confirmed_rs:
        vs.micro_avg_refined_r = statistics.mean(confirmed_rs)

    for d in sorted(day_trades.keys()):
        vs.daily_pnls.append(sum(t.pnl_dollars for t in day_trades[d]))

    return vs


MICRO_VARIANTS = [
    ("micro_strict_pm30", "strict", FirmConfig(pm_threshold=30.0, require_setup=True)),
    ("micro_fallback_pm30", "fallback", FirmConfig(pm_threshold=30.0, require_setup=True)),
    ("micro_strict_pm40", "strict", FirmConfig(pm_threshold=40.0, require_setup=True)),
    ("micro_fallback_pm40", "fallback", FirmConfig(pm_threshold=40.0, require_setup=True)),
    ("v3_baseline_pm30", "baseline", FirmConfig(pm_threshold=30.0, require_setup=True)),
    ("v3_baseline_pm40", "baseline", FirmConfig(pm_threshold=40.0, require_setup=True)),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch 14 — Micro real-tape backtest")
    parser.add_argument("--max-days", type=int, default=0)
    args = parser.parse_args()

    print("backtest_real_micro: loading Databento 1m tape...")
    t0 = time.monotonic()
    days = load_databento_days(days_tail=args.max_days or None)
    load_s = time.monotonic() - t0
    print(f"  loaded {len(days)} RTH days in {load_s:.1f}s")
    if not days:
        print("  ERROR: no days loaded")
        sys.exit(1)

    first_date = days[0][0].ts.date()
    last_date = days[-1][0].ts.date()
    print(f"  date range: {first_date} → {last_date}")

    print("  converting 1m → V3 format + aggregating to 5m...")
    day_data: list[tuple[str, list[V3Bar], list[V3Bar]]] = []
    dropped = 0
    for day_bars in days:
        day_date = day_bars[0].ts.strftime("%Y-%m-%d")
        bars_1m_v3 = _scrub_v3_day([_mnq_to_v3(b) for b in day_bars])
        if len(bars_1m_v3) < 350:
            dropped += 1
            continue
        bars_5m = _aggregate_1m_to_5m(bars_1m_v3)
        if len(bars_5m) < 60:
            dropped += 1
            continue
        day_data.append((day_date, bars_1m_v3, bars_5m))
    print(f"  {len(day_data)} clean days ({dropped} dropped)")

    det_cfg = V1DetectorConfig(
        exit_mode="fibonacci",
        use_partials=True,
        entry_mode="pullback",
        ema_tod_filter="Power Hours",
        ema_dow_filter="All Days",
    )
    refiner = MicroEntryRefiner()

    all_stats: list[MicroStats] = []
    daily_pnl_data: dict[str, dict[str, float]] = {}

    for vi, (name, micro_mode, firm_cfg) in enumerate(MICRO_VARIANTS):
        print(f"\n  [{vi + 1}/{len(MICRO_VARIANTS)}] {name}...", flush=True)
        t2 = time.monotonic()
        all_trades: list[MicroTrade] = []

        for day_date, bars_1m_v3, bars_5m in day_data:
            day_trades = _backtest_micro_day(
                micro_mode,
                firm_cfg,
                det_cfg,
                refiner,
                bars_1m_v3,
                bars_5m,
                day_date,
            )
            all_trades.extend(day_trades)

        vs = _compute_micro_stats(name, all_trades, total_days=len(day_data))
        all_stats.append(vs)

        day_pnl_map: dict[str, float] = {d: 0.0 for d, _, _ in day_data}
        for t in all_trades:
            day_pnl_map[t.day_date] = day_pnl_map.get(t.day_date, 0.0) + t.pnl_dollars
        daily_pnl_data[name] = day_pnl_map

        elapsed = time.monotonic() - t2
        wr = vs.winners / vs.total_trades * 100 if vs.total_trades else 0
        gw = sum(t.pnl_r for t in all_trades if t.pnl_r > 0)
        gl = abs(sum(t.pnl_r for t in all_trades if t.pnl_r < 0))
        pf = gw / gl if gl > 0 else float("inf")
        print(
            f"    {vs.total_trades} trades (micro: {vs.micro_confirmed_count} confirmed, "
            f"{vs.micro_skipped_count} skipped)"
        )
        print(
            f"    R={vs.total_pnl_r:+.2f}, ${vs.total_pnl_dollars:+.2f}, "
            f"WR {wr:.0f}%, PF {pf:.2f}, MDD {vs.max_drawdown_r:.2f}R, {elapsed:.1f}s"
        )
        if vs.micro_avg_refined_r > 0:
            print(f"    avg micro R improvement: {vs.micro_avg_refined_r:.2f}x")
        for setup, st in vs.by_setup.items():
            setup_wr = st["wins"] / st["trades"] * 100 if st["trades"] else 0
            print(
                f"      {setup:8s}: {st['trades']:3d} trades  {setup_wr:5.1f}% win  {st['total_r']:+.2f}R"
            )

    # ─── Reports ───
    report_dir = REPO_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)

    lines = [
        f"# Batch 14 — Micro Entry Refinement — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "MicroEntryRefiner on V3 signals, Databento MNQ tape.",
        f"**{len(day_data)} clean RTH days** ({first_date} → {last_date})",
        "",
        "## Variant Summary",
        "",
        "| Variant | Trades | Micro✓ | Micro✗ | W | L | WR% | Total R | PF | MaxDD R | $ PnL | Avg µR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for vs in sorted(all_stats, key=lambda x: x.total_pnl_r, reverse=True):
        wr = vs.winners / vs.total_trades * 100 if vs.total_trades else 0
        gw = sum(t.pnl_r for t in vs.trades if t.pnl_r > 0)
        gl = abs(sum(t.pnl_r for t in vs.trades if t.pnl_r < 0))
        pf = gw / gl if gl > 0 else float("inf")
        pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
        lines.append(
            f"| {vs.name} | {vs.total_trades} | {vs.micro_confirmed_count} | {vs.micro_skipped_count} "
            f"| {vs.winners} | {vs.losers} | {wr:.1f} | {vs.total_pnl_r:+.2f} | {pf_str} "
            f"| {vs.max_drawdown_r:.2f} | ${vs.total_pnl_dollars:+,.2f} | {vs.micro_avg_refined_r:.2f}x |"
        )

    # Micro confirmation rate analysis
    lines.extend(["", "## Micro Confirmation Analysis", ""])
    for vs in all_stats:
        if vs.micro_confirmed_count > 0:
            confirmed_trades = [t for t in vs.trades if t.micro_confirmed]
            unconfirmed_trades = [t for t in vs.trades if not t.micro_confirmed]
            c_wr = (
                sum(1 for t in confirmed_trades if t.pnl_r > 0) / len(confirmed_trades) * 100
                if confirmed_trades
                else 0
            )
            c_r = sum(t.pnl_r for t in confirmed_trades)
            u_wr = (
                sum(1 for t in unconfirmed_trades if t.pnl_r > 0) / len(unconfirmed_trades) * 100
                if unconfirmed_trades
                else 0
            )
            u_r = sum(t.pnl_r for t in unconfirmed_trades)
            lines.append(f"**{vs.name}:**")
            lines.append(
                f"  - Confirmed: {len(confirmed_trades)} trades, {c_wr:.0f}% WR, {c_r:+.2f}R"
            )
            lines.append(
                f"  - Unconfirmed: {len(unconfirmed_trades)} trades, {u_wr:.0f}% WR, {u_r:+.2f}R"
            )
            lines.append("")

    # Verdict
    lines.extend(["## Verdict", ""])
    baseline_pm30 = next((s for s in all_stats if s.name == "v3_baseline_pm30"), None)
    strict_pm30 = next((s for s in all_stats if s.name == "micro_strict_pm30"), None)
    next((s for s in all_stats if s.name == "micro_fallback_pm30"), None)

    if baseline_pm30 and strict_pm30:
        lift_r = strict_pm30.total_pnl_r - baseline_pm30.total_pnl_r
        if lift_r > 0:
            lines.append(
                f"**MICRO REFINEMENT ADDS VALUE** — strict micro at PM30 gives "
                f"{lift_r:+.2f}R lift over baseline."
            )
        else:
            lines.append(
                f"**MICRO REFINEMENT NO LIFT** — strict micro at PM30 gives "
                f"{lift_r:+.2f}R vs baseline. Micro gating may be too aggressive."
            )

    lines.append("")
    lines.append(f"*Generated in {time.monotonic() - t0:.1f}s*")

    (report_dir / "backtest_real_micro.md").write_text("\n".join(lines))
    print("\nWrote reports/backtest_real_micro.md")

    # Trade log CSV
    csv_lines = [
        "variant,date,setup,side,micro_mode,micro_confirmed,micro_reason,"
        "micro_confidence,micro_bars_waited,micro_refined_r,"
        "entry_px,stop,tp1,tp2,sl_dist,pm_final,regime,"
        "exit_px,exit_reason,pnl_r,pnl_dollars,mfe_r,mae_r"
    ]
    for vs in all_stats:
        for t in vs.trades:
            csv_lines.append(
                f"{vs.name},{t.day_date},{t.setup},{t.side},{t.micro_mode},"
                f"{t.micro_confirmed},{t.micro_reason},"
                f"{t.micro_confidence:.2f},{t.micro_bars_waited},{t.micro_refined_r:.2f},"
                f"{t.entry_price:.2f},{t.stop:.2f},{t.tp1:.2f},{t.tp2:.2f},{t.sl_dist:.2f},"
                f"{t.pm_final:.1f},{t.regime},"
                f"{t.exit_price:.2f},{t.exit_reason},{t.pnl_r:.3f},{t.pnl_dollars:.2f},"
                f"{t.mfe_r:.2f},{t.mae_r:.2f}"
            )
    (report_dir / "backtest_real_micro_trades.csv").write_text("\n".join(csv_lines))
    print(f"Wrote reports/backtest_real_micro_trades.csv ({len(csv_lines) - 1} trades)")

    # Daily PnL JSON
    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "backtest_real_micro_daily.json").write_text(json.dumps(daily_pnl_data, indent=2))
    print("Wrote data/backtest_real_micro_daily.json")


if __name__ == "__main__":
    main()
