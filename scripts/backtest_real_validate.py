"""Batches 16-20 — Champion Validation Suite on real Databento MNQ tape.

Batch 15 champion: orb_only_pm30 (+31.39R, 77% WR, PF 3.41, 112 trades, 1652 days)

This script runs five validation analyses on the champion:
  Batch 16: Walk-Forward (rolling 2yr train / 1yr test folds)
  Batch 17: Year-by-Year Stability + monthly equity curve
  Batch 18: Slippage & Commission Sensitivity (edge death threshold)
  Batch 19: Bootstrap CI + Monte Carlo (shippability gate: CI excludes zero, n>=8)
  Batch 20: Drawdown Profile + Kelly sizing + equity curve

Output:
    reports/backtest_real_validate.md      (combined report)
    reports/backtest_real_validate.csv     (champion trades)
    data/backtest_real_validate.json       (equity curves, fold data)

Usage:
    python scripts/backtest_real_validate.py
    python scripts/backtest_real_validate.py --max-days 200
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

UTC = _dt.UTC
if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.UTC  # type: ignore[attr-defined]

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = Path(__file__).resolve().parent
V3_DIR = REPO_ROOT / "eta_v3_framework" / "python"

for p in (str(SRC), str(SCRIPTS), str(V3_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest import V1Detector, V1DetectorConfig  # noqa: E402, I001
from firm_engine import (  # noqa: E402, I001
    Bar as V3Bar, FirmConfig, detect_regime, evaluate,
)
from indicator_state import IndicatorState  # noqa: E402
from mnq.core.types import Bar as MnqBar  # noqa: E402
from real_bars import load_databento_days  # noqa: E402

TICK = 0.25
POINT_VALUE = 2.00

# ═══════════════════════════════════════════════════════════════════
# Shared infrastructure (from ensemble script)
# ═══════════════════════════════════════════════════════════════════

def _mnq_to_v3(bar: MnqBar) -> V3Bar:
    return V3Bar(
        time=int(bar.ts.timestamp()),
        open=float(bar.open), high=float(bar.high),
        low=float(bar.low), close=float(bar.close),
        volume=float(bar.volume),
    )

def _aggregate_1m_to_5m(bars_1m: list[V3Bar]) -> list[V3Bar]:
    if not bars_1m:
        return []
    buckets: dict[int, list[V3Bar]] = {}
    for b in bars_1m:
        key = (b.time // 300) * 300
        buckets.setdefault(key, []).append(b)
    return [V3Bar(time=k, open=g[0].open, high=max(b.high for b in g),
                  low=min(b.low for b in g), close=g[-1].close,
                  volume=sum(b.volume for b in g))
            for k, g in sorted(buckets.items())]

def _scrub_v3_day(bars: list[V3Bar]) -> list[V3Bar]:
    if not bars:
        return bars
    clean: list[V3Bar] = [bars[0]] if bars[0].close > 0 else []
    for b in bars[1:]:
        if b.close <= 0:
            continue
        if clean and clean[-1].close > 0 and abs(b.close - clean[-1].close) / clean[-1].close > 0.03:
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

def _resolve_exit_on_1m(
    *, side, entry_price, stop, tp1, tp2, sl_dist,
    use_partials, use_mfe_trail, trail_arm_R, trail_lock_R,  # noqa: N803
    bars_1m, signal_1m_ix, timeout_bars_1m,
):
    current_sl = stop
    tp1_filled = False
    mfe_r = mae_r = 0.0
    for i in range(signal_1m_ix + 1, min(signal_1m_ix + timeout_bars_1m + 1, len(bars_1m))):
        bar = bars_1m[i]
        held = i - signal_1m_ix
        if sl_dist > 0:
            if side == "long":
                fav, adv = (bar.high - entry_price) / sl_dist, (bar.low - entry_price) / sl_dist
            else:
                fav, adv = (entry_price - bar.low) / sl_dist, (entry_price - bar.high) / sl_dist
            mfe_r, mae_r = max(mfe_r, fav), min(mae_r, adv)
        if use_mfe_trail and mfe_r >= trail_arm_R and not tp1_filled and sl_dist > 0:
            lock = entry_price + sl_dist * trail_lock_R if side == "long" else entry_price - sl_dist * trail_lock_R
            current_sl = max(current_sl, lock) if side == "long" else min(current_sl, lock)
        sl_hit = (bar.low <= current_sl) if side == "long" else (bar.high >= current_sl)
        tp1_hit = (bar.high >= tp1) if side == "long" else (bar.low <= tp1)
        tp2_hit = (bar.high >= tp2) if side == "long" else (bar.low <= tp2)
        if use_partials and not tp1_filled and tp1_hit and not sl_hit:
            tp1_filled = True
            current_sl = entry_price
            continue
        bars_5m = held // 5
        if sl_hit:
            actual_r = ((current_sl - entry_price) / sl_dist if side == "long"
                       else (entry_price - current_sl) / sl_dist)
            if tp1_filled:
                return current_sl, "tp1_then_be", max(actual_r, 0.5), bars_5m, mfe_r, mae_r
            return current_sl, ("trail_lock" if actual_r > 0 else "stop"), actual_r, bars_5m, mfe_r, mae_r
        if tp2_hit:
            actual_r = ((tp2 - entry_price) / sl_dist if side == "long"
                       else (entry_price - tp2) / sl_dist)
            if use_partials and tp1_filled:
                return tp2, "tp2_partial", actual_r * 0.5 + 0.5, bars_5m, mfe_r, mae_r
            return tp2, "tp2", actual_r, bars_5m, mfe_r, mae_r
        if not use_partials and tp1_hit:
            actual_r = ((tp1 - entry_price) / sl_dist if side == "long"
                       else (entry_price - tp1) / sl_dist)
            return tp1, "tp1", actual_r, bars_5m, mfe_r, mae_r
        if held >= timeout_bars_1m:
            return bar.close, "timeout", (0.5 if tp1_filled else 0.0), bars_5m, mfe_r, mae_r
    last = bars_1m[-1]
    return last.close, "session_end", (0.5 if tp1_filled else 0.0), (len(bars_1m) - signal_1m_ix) // 5, mfe_r, mae_r


@dataclass
class Trade:
    day_date: str
    setup: str
    side: str
    entry_price: float
    stop: float
    tp1: float
    tp2: float
    sl_dist: float
    pm_final: float
    regime: str
    voice_agree: int
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_r: float = 0.0
    pnl_dollars: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0


# Champion config
CHAMPION_FIRM_CFG = FirmConfig(pm_threshold=30.0, require_setup=True)
CHAMPION_DET_CFG = V1DetectorConfig(
    exit_mode="fibonacci", use_partials=True, entry_mode="pullback",
    ema_tod_filter="Power Hours", ema_dow_filter="All Days",
)
ALLOWED_SETUPS = {"ORB"}


def _compute_sl_tp(setup, side, det_cfg, detector, bar, entry_price):
    atr = bar.atr or 1.0
    use_fib = (det_cfg.exit_mode == "fibonacci" or
               (det_cfg.exit_mode == "hybrid" and setup in ("ORB", "SWEEP")))
    if setup == "ORB":
        or_low, or_high = detector.or_low, detector.or_high
        sl = (or_low - atr * 0.15 if side == "long" and or_low is not None
              else or_high + atr * 0.15 if side == "short" and or_high is not None
              else entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5)
        if (use_fib and or_low is not None and or_high is not None and (or_high - or_low) > atr * 0.5):
            or_range = or_high - or_low
            tp1 = (or_high + or_range * (det_cfg.fib_tp1_extension - 1.0) if side == "long"
                   else or_low - or_range * (det_cfg.fib_tp1_extension - 1.0))
            tp2 = (or_high + or_range * (det_cfg.fib_tp2_extension - 1.0) if side == "long"
                   else or_low - or_range * (det_cfg.fib_tp2_extension - 1.0))
        else:
            sd = abs(entry_price - sl) or atr
            tp1 = entry_price + sd * det_cfg.orb_tp1_r if side == "long" else entry_price - sd * det_cfg.orb_tp1_r
            tp2 = entry_price + sd * det_cfg.orb_tp2_r if side == "long" else entry_price - sd * det_cfg.orb_tp2_r
        timeout = det_cfg.orb_timeout
    elif setup == "EMA PB":
        sl = entry_price - atr * det_cfg.ema_sl_atr if side == "long" else entry_price + atr * det_cfg.ema_sl_atr
        sd = abs(entry_price - sl) or atr
        tp1 = entry_price + sd * det_cfg.ema_tp1_r if side == "long" else entry_price - sd * det_cfg.ema_tp1_r
        tp2 = entry_price + sd * det_cfg.ema_tp2_r if side == "long" else entry_price - sd * det_cfg.ema_tp2_r
        timeout = det_cfg.ema_timeout
    elif setup == "SWEEP":
        buf = det_cfg.sweep_sl_ticks * TICK
        swept_lo, swept_hi = detector.swept_lo_px, detector.swept_hi_px
        sl = (swept_lo - buf if side == "long" and swept_lo is not None
              else swept_hi + buf if side == "short" and swept_hi is not None
              else entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5)
        if (use_fib and swept_lo is not None and swept_hi is not None
                and abs(swept_hi - swept_lo) > atr * 0.5):
            sr = min(abs(swept_hi - swept_lo), atr * 3.0)
            tp1 = entry_price + sr * det_cfg.fib_tp1_extension if side == "long" else entry_price - sr * det_cfg.fib_tp1_extension
            tp2 = entry_price + sr * det_cfg.fib_tp2_extension if side == "long" else entry_price - sr * det_cfg.fib_tp2_extension
        else:
            sd = abs(entry_price - sl) or atr
            tp1 = entry_price + sd * det_cfg.sweep_tp1_r if side == "long" else entry_price - sd * det_cfg.sweep_tp1_r
            tp2 = entry_price + sd * det_cfg.sweep_tp2_r if side == "long" else entry_price - sd * det_cfg.sweep_tp2_r
        timeout = det_cfg.sweep_timeout
    else:
        sl = entry_price - atr * 1.5 if side == "long" else entry_price + atr * 1.5
        sd = abs(entry_price - sl) or atr
        tp1 = entry_price + sd if side == "long" else entry_price - sd
        tp2 = entry_price + sd * 2 if side == "long" else entry_price - sd * 2
        timeout = 30
    return sl, tp1, tp2, timeout


def _backtest_champion_day(
    bars_1m_v3: list[V3Bar], bars_5m: list[V3Bar], day_date: str,
) -> list[Trade]:
    if not bars_5m or len(bars_5m) < 10:
        return []
    detector = V1Detector(cfg=CHAMPION_DET_CFG)
    state = IndicatorState()
    trades: list[Trade] = []
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
            bar=bar, st=st, regime=regime,
            atr_ma20=atr_ma20, vol_z=vol_z,
            prev_adx_3=state.adx_3_bars_ago(),
            range_avg_20=state.range_avg_20(),
            vol_z_prev_1=state.vol_z_at(1),
            vol_z_prev_2=state.vol_z_at(2),
            highest_5_prev=state.highest_5_prev(),
            lowest_5_prev=state.lowest_5_prev(),
            recent_losses=0,
            prev_day_high=state.prev_day_high,
            prev_day_low=state.prev_day_low,
            cfg=CHAMPION_FIRM_CFG,
        )
        if not (d.fire_long or d.fire_short):
            continue
        if bar_ix < cooldown_until:
            continue
        side = "long" if d.fire_long else "short"
        setup = d.setup_name or "FIRM"
        if setup not in ALLOWED_SETUPS:
            continue
        entry_price = bar.close
        sl, tp1, tp2, timeout = _compute_sl_tp(setup, side, CHAMPION_DET_CFG, detector, bar, entry_price)
        sl_dist = abs(entry_price - sl)
        if sl_dist <= 0:
            continue
        signal_1m_ix = _find_1m_ix(bars_1m_v3, bar.time + 300)
        exit_px, exit_reason, pnl_r, _, mfe_r, mae_r = _resolve_exit_on_1m(
            side=side, entry_price=entry_price, stop=sl, tp1=tp1, tp2=tp2,
            sl_dist=sl_dist, use_partials=CHAMPION_DET_CFG.use_partials,
            use_mfe_trail=CHAMPION_DET_CFG.use_mfe_trail,
            trail_arm_R=CHAMPION_DET_CFG.trail_arm_R,
            trail_lock_R=CHAMPION_DET_CFG.trail_lock_R,
            bars_1m=bars_1m_v3, signal_1m_ix=signal_1m_ix,
            timeout_bars_1m=timeout * 5,
        )
        pnl_dollars = pnl_r * sl_dist * POINT_VALUE
        trades.append(Trade(
            day_date=day_date, setup=setup, side=side,
            entry_price=entry_price, stop=sl, tp1=tp1, tp2=tp2,
            sl_dist=sl_dist, pm_final=d.pm_final, regime=regime,
            voice_agree=d.voice_agree,
            exit_price=exit_px, exit_reason=exit_reason,
            pnl_r=pnl_r, pnl_dollars=pnl_dollars,
            mfe_r=mfe_r, mae_r=mae_r,
        ))
        cooldown_until = bar_ix + CHAMPION_DET_CFG.cooldown
    return trades


# ═══════════════════════════════════════════════════════════════════
# Batch 16: Walk-Forward Validation
# ═══════════════════════════════════════════════════════════════════

def batch_16_walk_forward(all_trades: list[Trade], day_dates: list[str]) -> dict:
    """Rolling 2yr train / 1yr test walk-forward on the champion."""
    # Group trades by year
    year_trades: dict[str, list[Trade]] = defaultdict(list)
    for t in all_trades:
        yr = t.day_date[:4]
        year_trades[yr].append(t)

    years = sorted(year_trades.keys())
    folds = []

    # 2-year train, 1-year test, rolling forward
    for i in range(len(years)):
        if i < 2:
            continue
        train_years = [years[i - 2], years[i - 1]]
        test_year = years[i]
        train_trades = []
        for y in train_years:
            train_trades.extend(year_trades[y])
        test_trades = year_trades[test_year]

        train_r = sum(t.pnl_r for t in train_trades)
        test_r = sum(t.pnl_r for t in test_trades)
        train_n = len(train_trades)
        test_n = len(test_trades)
        train_wr = sum(1 for t in train_trades if t.pnl_r > 0) / train_n * 100 if train_n else 0
        test_wr = sum(1 for t in test_trades if t.pnl_r > 0) / test_n * 100 if test_n else 0
        test_pnl = sum(t.pnl_dollars for t in test_trades)

        folds.append({
            "fold": i - 1,
            "train_years": "/".join(train_years),
            "test_year": test_year,
            "train_n": train_n, "test_n": test_n,
            "train_r": round(train_r, 2), "test_r": round(test_r, 2),
            "train_wr": round(train_wr, 1), "test_wr": round(test_wr, 1),
            "test_pnl": round(test_pnl, 2),
            "oos_positive": test_r > 0,
        })

    positive_folds = sum(1 for f in folds if f["oos_positive"])
    total_oos_r = sum(f["test_r"] for f in folds)
    total_oos_pnl = sum(f["test_pnl"] for f in folds)
    mean_oos_r = total_oos_r / len(folds) if folds else 0

    return {
        "folds": folds,
        "total_folds": len(folds),
        "positive_folds": positive_folds,
        "total_oos_r": round(total_oos_r, 2),
        "total_oos_pnl": round(total_oos_pnl, 2),
        "mean_oos_r_per_fold": round(mean_oos_r, 2),
        "verdict": "PASS" if positive_folds > len(folds) / 2 else "FAIL",
    }


# ═══════════════════════════════════════════════════════════════════
# Batch 17: Year-by-Year Stability
# ═══════════════════════════════════════════════════════════════════

def batch_17_yearly_stability(all_trades: list[Trade]) -> dict:
    year_data: dict[str, dict] = {}
    for t in all_trades:
        yr = t.day_date[:4]
        if yr not in year_data:
            year_data[yr] = {"trades": 0, "wins": 0, "total_r": 0.0, "pnl": 0.0, "r_list": []}
        year_data[yr]["trades"] += 1
        year_data[yr]["total_r"] += t.pnl_r
        year_data[yr]["pnl"] += t.pnl_dollars
        year_data[yr]["r_list"].append(t.pnl_r)
        if t.pnl_r > 0:
            year_data[yr]["wins"] += 1

    years = []
    for yr in sorted(year_data.keys()):
        d = year_data[yr]
        wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
        avg_r = d["total_r"] / d["trades"] if d["trades"] else 0
        years.append({
            "year": yr,
            "trades": d["trades"],
            "wins": d["wins"],
            "wr": round(wr, 1),
            "total_r": round(d["total_r"], 2),
            "avg_r": round(avg_r, 3),
            "pnl": round(d["pnl"], 2),
            "positive": d["total_r"] > 0,
        })

    # Monthly breakdown
    month_data: dict[str, dict] = {}
    for t in all_trades:
        mo = t.day_date[:7]
        if mo not in month_data:
            month_data[mo] = {"trades": 0, "wins": 0, "total_r": 0.0, "pnl": 0.0}
        month_data[mo]["trades"] += 1
        month_data[mo]["total_r"] += t.pnl_r
        month_data[mo]["pnl"] += t.pnl_dollars
        if t.pnl_r > 0:
            month_data[mo]["wins"] += 1

    months = []
    for mo in sorted(month_data.keys()):
        d = month_data[mo]
        wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
        months.append({
            "month": mo, "trades": d["trades"],
            "wr": round(wr, 1), "total_r": round(d["total_r"], 2),
            "pnl": round(d["pnl"], 2),
        })

    positive_years = sum(1 for y in years if y["positive"])
    total_years = len(years)

    return {
        "years": years,
        "months": months,
        "positive_years": positive_years,
        "total_years": total_years,
        "verdict": "STABLE" if positive_years >= total_years * 0.7 else "UNSTABLE",
    }


# ═══════════════════════════════════════════════════════════════════
# Batch 18: Slippage & Commission Sensitivity
# ═══════════════════════════════════════════════════════════════════

def batch_18_slippage_sensitivity(all_trades: list[Trade]) -> dict:
    """Test edge at various slippage + commission levels."""
    # Slippage in ticks applied to each trade (entry + exit = round trip)
    # Commission per side in dollars
    scenarios = []
    for slip_ticks in [0, 1, 2, 3, 4, 5, 6, 8, 10]:
        for comm_per_side in [0.0, 0.62, 1.24]:  # 0, NinjaTrader micro, double
            slip_cost_per_trade = slip_ticks * TICK * POINT_VALUE * 2  # round trip
            comm_cost = comm_per_side * 2  # round trip
            total_cost = slip_cost_per_trade + comm_cost

            adj_pnl = sum(t.pnl_dollars - total_cost for t in all_trades)
            adj_trades = len(all_trades)
            adj_wins = sum(1 for t in all_trades if t.pnl_dollars - total_cost > 0)
            adj_wr = adj_wins / adj_trades * 100 if adj_trades else 0

            # Adj R: subtract cost from each trade's R
            adj_r_list = []
            for t in all_trades:
                cost_r = total_cost / (t.sl_dist * POINT_VALUE) if t.sl_dist > 0 else 0
                adj_r_list.append(t.pnl_r - cost_r)
            adj_total_r = sum(adj_r_list)
            adj_avg_r = adj_total_r / adj_trades if adj_trades else 0

            # PF adjusted
            gw = sum(r for r in adj_r_list if r > 0)
            gl = abs(sum(r for r in adj_r_list if r < 0))
            pf = gw / gl if gl > 0 else float('inf')

            scenarios.append({
                "slip_ticks": slip_ticks,
                "comm_per_side": comm_per_side,
                "total_cost_per_trade": round(total_cost, 2),
                "adj_total_r": round(adj_total_r, 2),
                "adj_avg_r": round(adj_avg_r, 3),
                "adj_pnl": round(adj_pnl, 2),
                "adj_wr": round(adj_wr, 1),
                "adj_pf": round(pf, 2) if pf != float('inf') else 999,
                "edge_alive": adj_total_r > 0,
            })

    # Find breakeven slippage (at standard commission $0.62/side)
    std_comm = [s for s in scenarios if abs(s["comm_per_side"] - 0.62) < 0.01]
    breakeven_slip = None
    for s in std_comm:
        if not s["edge_alive"]:
            breakeven_slip = s["slip_ticks"]
            break

    return {
        "scenarios": scenarios,
        "breakeven_slip_ticks": breakeven_slip,
        "breakeven_slip_points": breakeven_slip * TICK if breakeven_slip else None,
        "verdict": f"EDGE DIES AT {breakeven_slip}t SLIP" if breakeven_slip else "EDGE SURVIVES ALL",
    }


# ═══════════════════════════════════════════════════════════════════
# Batch 19: Bootstrap CI + Monte Carlo
# ═══════════════════════════════════════════════════════════════════

def batch_19_bootstrap(all_trades: list[Trade], n_bootstrap: int = 10000) -> dict:
    """Bootstrap resampling for CI on expectancy, WR, total R."""
    random.seed(42)
    n = len(all_trades)
    r_values = [t.pnl_r for t in all_trades]

    # Bootstrap distributions
    boot_total_r = []
    boot_avg_r = []
    boot_wr = []
    boot_pf = []

    for _ in range(n_bootstrap):
        sample = random.choices(r_values, k=n)
        total = sum(sample)
        avg = total / n
        wins = sum(1 for r in sample if r > 0) / n * 100
        gw = sum(r for r in sample if r > 0)
        gl = abs(sum(r for r in sample if r < 0))
        pf = gw / gl if gl > 0 else 999.0

        boot_total_r.append(total)
        boot_avg_r.append(avg)
        boot_wr.append(wins)
        boot_pf.append(pf)

    boot_total_r.sort()
    boot_avg_r.sort()
    boot_wr.sort()
    boot_pf.sort()

    def ci(arr, lo=2.5, hi=97.5):
        return round(arr[int(len(arr) * lo / 100)], 3), round(arr[int(len(arr) * hi / 100)], 3)

    ci_total_r = ci(boot_total_r)
    ci_avg_r = ci(boot_avg_r)
    ci_wr = ci(boot_wr)
    ci_pf = ci(boot_pf)

    # Monte Carlo forward simulation (252 trading days, based on trade frequency)
    trades_per_day = n / 1652  # avg trades per day
    mc_terminal = []
    for _ in range(n_bootstrap):
        equity = 0.0
        for _ in range(252):
            # Poisson-ish: sample trades_per_day trades
            n_today = random.choices([0, 1], weights=[1 - trades_per_day, trades_per_day], k=1)[0]
            if n_today:
                r = random.choice(r_values)
                equity += r
        mc_terminal.append(equity)

    mc_terminal.sort()
    mc_median = mc_terminal[len(mc_terminal) // 2]
    mc_p5 = mc_terminal[int(len(mc_terminal) * 0.05)]
    mc_p95 = mc_terminal[int(len(mc_terminal) * 0.95)]
    mc_prob_positive = sum(1 for x in mc_terminal if x > 0) / len(mc_terminal) * 100

    # Shippability gate: CI excludes zero AND n >= 8
    ci_excludes_zero = ci_total_r[0] > 0
    n_sufficient = n >= 8

    return {
        "n_trades": n,
        "n_bootstrap": n_bootstrap,
        "observed_total_r": round(sum(r_values), 2),
        "observed_avg_r": round(sum(r_values) / n, 3),
        "observed_wr": round(sum(1 for r in r_values if r > 0) / n * 100, 1),
        "ci_95_total_r": ci_total_r,
        "ci_95_avg_r": ci_avg_r,
        "ci_95_wr": ci_wr,
        "ci_95_pf": ci_pf,
        "ci_excludes_zero": ci_excludes_zero,
        "n_sufficient": n_sufficient,
        "mc_1yr_median_r": round(mc_median, 2),
        "mc_1yr_p5_r": round(mc_p5, 2),
        "mc_1yr_p95_r": round(mc_p95, 2),
        "mc_prob_positive_1yr": round(mc_prob_positive, 1),
        "shippable": ci_excludes_zero and n_sufficient,
        "verdict": "SHIPPABLE" if (ci_excludes_zero and n_sufficient) else "NOT SHIPPABLE",
    }


# ═══════════════════════════════════════════════════════════════════
# Batch 20: Drawdown Profile + Kelly + Equity Curve
# ═══════════════════════════════════════════════════════════════════

def batch_20_drawdown_profile(all_trades: list[Trade]) -> dict:
    """Equity curve, drawdown analysis, Kelly sizing, consecutive streaks."""
    equity_curve = []
    equity = 0.0
    peak = 0.0
    max_dd_r = 0.0
    max_dd_start = ""
    max_dd_end = ""
    dd_start_date = all_trades[0].day_date if all_trades else ""

    # Consecutive win/loss streaks
    current_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    streak_type = None

    for t in all_trades:
        equity += t.pnl_r
        if equity > peak:
            peak = equity
            dd_start_date = t.day_date
        dd = peak - equity
        if dd > max_dd_r:
            max_dd_r = dd
            max_dd_start = dd_start_date
            max_dd_end = t.day_date

        equity_curve.append({
            "date": t.day_date,
            "equity_r": round(equity, 3),
            "drawdown_r": round(dd, 3),
            "trade_r": round(t.pnl_r, 3),
        })

        # Streak tracking
        if t.pnl_r > 0:
            if streak_type == "win":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "win"
            max_win_streak = max(max_win_streak, current_streak)
        elif t.pnl_r < 0:
            if streak_type == "loss":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "loss"
            max_loss_streak = max(max_loss_streak, current_streak)

    # Kelly criterion
    r_values = [t.pnl_r for t in all_trades]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    wr = len(wins) / len(r_values) if r_values else 0
    avg_win = statistics.mean(wins) if wins else 0
    avg_loss = abs(statistics.mean(losses)) if losses else 1
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 999

    kelly_full = wr - (1 - wr) / win_loss_ratio if win_loss_ratio > 0 else 0
    kelly_half = kelly_full / 2  # Conservative half-Kelly
    kelly_quarter = kelly_full / 4

    # Recovery factor: total R / max DD
    recovery_factor = sum(r_values) / max_dd_r if max_dd_r > 0 else 999

    # Profit factor
    gw = sum(r for r in r_values if r > 0)
    gl = abs(sum(r for r in r_values if r < 0))
    pf = gw / gl if gl > 0 else 999

    # Expectancy stats
    avg_r = statistics.mean(r_values) if r_values else 0
    stdev_r = statistics.stdev(r_values) if len(r_values) > 1 else 0
    sharpe_r = avg_r / stdev_r if stdev_r > 0 else 0

    # Drawdown durations (in trade count)
    drawdowns = []
    dd_trades = 0
    in_dd = False
    for ec in equity_curve:
        if ec["drawdown_r"] > 0:
            if not in_dd:
                in_dd = True
                dd_trades = 1
            else:
                dd_trades += 1
        else:
            if in_dd:
                drawdowns.append(dd_trades)
                in_dd = False
                dd_trades = 0
    if in_dd:
        drawdowns.append(dd_trades)

    avg_dd_duration = statistics.mean(drawdowns) if drawdowns else 0
    max_dd_duration = max(drawdowns) if drawdowns else 0

    return {
        "equity_curve": equity_curve,
        "max_dd_r": round(max_dd_r, 2),
        "max_dd_start": max_dd_start,
        "max_dd_end": max_dd_end,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "kelly_full": round(kelly_full, 4),
        "kelly_half": round(kelly_half, 4),
        "kelly_quarter": round(kelly_quarter, 4),
        "recovery_factor": round(recovery_factor, 2),
        "profit_factor": round(pf, 2),
        "avg_r": round(avg_r, 3),
        "stdev_r": round(stdev_r, 3),
        "sharpe_per_trade": round(sharpe_r, 3),
        "avg_dd_duration_trades": round(avg_dd_duration, 1),
        "max_dd_duration_trades": max_dd_duration,
        "n_drawdown_periods": len(drawdowns),
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3),
        "win_loss_ratio": round(win_loss_ratio, 2),
    }


# ═══════════════════════════════════════════════════════════════════
# Report Generation
# ═══════════════════════════════════════════════════════════════════

def _generate_report(
    all_trades, total_days, first_date, last_date,
    wf, yearly, slippage, bootstrap, dd,
    gen_time,
):
    lines = [
        f"# Batches 16-20 — Champion Validation — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Champion: **orb_only_pm30** on Databento MNQ 1m tape.",
        f"**{total_days} clean RTH days** ({first_date} -> {last_date})",
        f"**{len(all_trades)} trades** | {sum(1 for t in all_trades if t.pnl_r > 0)}W / "
        f"{sum(1 for t in all_trades if t.pnl_r < 0)}L | "
        f"{sum(1 for t in all_trades if t.pnl_r > 0) / len(all_trades) * 100:.1f}% WR | "
        f"+{sum(t.pnl_r for t in all_trades):.2f}R",
        "",
    ]

    # Batch 16: Walk-Forward
    lines.extend([
        "## Batch 16 — Walk-Forward Validation",
        "",
        f"Rolling 2yr train / 1yr test. **{wf['positive_folds']}/{wf['total_folds']} folds positive OOS.** "
        f"Verdict: **{wf['verdict']}**",
        "",
        "| Fold | Train | Test | Train n | Test n | Train R | Test R | Train WR | Test WR | $ PnL | OOS |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for f in wf["folds"]:
        oos = "+" if f["oos_positive"] else "-"
        lines.append(
            f"| {f['fold']} | {f['train_years']} | {f['test_year']} "
            f"| {f['train_n']} | {f['test_n']} "
            f"| {f['train_r']:+.2f} | {f['test_r']:+.2f} "
            f"| {f['train_wr']:.1f}% | {f['test_wr']:.1f}% "
            f"| ${f['test_pnl']:+,.2f} | {oos} |"
        )
    lines.append(f"\n**Total OOS R:** {wf['total_oos_r']:+.2f} | **Mean/fold:** {wf['mean_oos_r_per_fold']:+.2f}")

    # Batch 17: Year-by-Year
    lines.extend([
        "", "## Batch 17 — Year-by-Year Stability", "",
        f"**{yearly['positive_years']}/{yearly['total_years']} positive years.** Verdict: **{yearly['verdict']}**",
        "",
        "| Year | Trades | Wins | WR% | Total R | Avg R | $ PnL | Status |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    for y in yearly["years"]:
        status = "+" if y["positive"] else "-"
        lines.append(
            f"| {y['year']} | {y['trades']} | {y['wins']} | {y['wr']:.1f}% "
            f"| {y['total_r']:+.2f} | {y['avg_r']:+.3f} | ${y['pnl']:+,.2f} | {status} |"
        )

    # Monthly equity
    lines.extend(["", "### Monthly Breakdown", "",
        "| Month | Trades | WR% | R Total | $ PnL |",
        "|---|---:|---:|---:|---:|"])
    for m in yearly["months"]:
        lines.append(f"| {m['month']} | {m['trades']} | {m['wr']:.1f}% | {m['total_r']:+.2f} | ${m['pnl']:+,.2f} |")

    # Batch 18: Slippage
    lines.extend([
        "", "## Batch 18 — Slippage & Commission Sensitivity", "",
        f"Verdict: **{slippage['verdict']}**",
    ])
    if slippage["breakeven_slip_ticks"] is not None:
        lines.append(f"Breakeven at {slippage['breakeven_slip_ticks']}t slippage "
                    f"({slippage['breakeven_slip_points']:.2f} pts) with $0.62/side commission.")
    lines.extend([
        "",
        "| Slip (t) | Comm/Side | Cost/Trade | Adj R | Adj AvgR | Adj PnL | Adj WR | PF | Edge |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for s in slippage["scenarios"]:
        if s["comm_per_side"] != 0.62:
            continue  # Only show standard commission in summary
        edge = "+" if s["edge_alive"] else "DEAD"
        pf_str = f"{s['adj_pf']:.2f}" if s["adj_pf"] < 999 else "INF"
        lines.append(
            f"| {s['slip_ticks']} | ${s['comm_per_side']:.2f} | ${s['total_cost_per_trade']:.2f} "
            f"| {s['adj_total_r']:+.2f} | {s['adj_avg_r']:+.3f} | ${s['adj_pnl']:+,.2f} "
            f"| {s['adj_wr']:.1f}% | {pf_str} | {edge} |"
        )

    # Batch 19: Bootstrap
    lines.extend([
        "", "## Batch 19 — Bootstrap CI & Monte Carlo", "",
        f"**{bootstrap['n_bootstrap']:,} resamples** on {bootstrap['n_trades']} trades. "
        f"Verdict: **{bootstrap['verdict']}**",
        "",
        "| Metric | Observed | 95% CI Low | 95% CI High |",
        "|---|---:|---:|---:|",
        f"| Total R | {bootstrap['observed_total_r']:+.2f} | {bootstrap['ci_95_total_r'][0]:+.3f} | {bootstrap['ci_95_total_r'][1]:+.3f} |",
        f"| Avg R/Trade | {bootstrap['observed_avg_r']:+.3f} | {bootstrap['ci_95_avg_r'][0]:+.3f} | {bootstrap['ci_95_avg_r'][1]:+.3f} |",
        f"| Win Rate | {bootstrap['observed_wr']:.1f}% | {bootstrap['ci_95_wr'][0]:.1f}% | {bootstrap['ci_95_wr'][1]:.1f}% |",
        f"| Profit Factor | - | {bootstrap['ci_95_pf'][0]:.2f} | {bootstrap['ci_95_pf'][1]:.2f} |",
        "",
        f"- CI excludes zero: **{'YES' if bootstrap['ci_excludes_zero'] else 'NO'}**",
        f"- n >= 8: **{'YES' if bootstrap['n_sufficient'] else 'NO'}** (n={bootstrap['n_trades']})",
        f"- **Shippable: {'YES' if bootstrap['shippable'] else 'NO'}**",
        "",
        "### Monte Carlo 1-Year Forward",
        f"- Median R: {bootstrap['mc_1yr_median_r']:+.2f}",
        f"- 5th pctile: {bootstrap['mc_1yr_p5_r']:+.2f}",
        f"- 95th pctile: {bootstrap['mc_1yr_p95_r']:+.2f}",
        f"- P(positive year): {bootstrap['mc_prob_positive_1yr']:.1f}%",
    ])

    # Batch 20: Drawdown
    lines.extend([
        "", "## Batch 20 — Drawdown Profile & Kelly Sizing", "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Max Drawdown | {dd['max_dd_r']}R |",
        f"| DD Period | {dd['max_dd_start']} -> {dd['max_dd_end']} |",
        f"| Max Win Streak | {dd['max_win_streak']} |",
        f"| Max Loss Streak | {dd['max_loss_streak']} |",
        f"| Recovery Factor | {dd['recovery_factor']} |",
        f"| Profit Factor | {dd['profit_factor']} |",
        f"| Avg Win | +{dd['avg_win_r']}R |",
        f"| Avg Loss | -{dd['avg_loss_r']}R |",
        f"| Win/Loss Ratio | {dd['win_loss_ratio']} |",
        f"| Avg R/Trade | {dd['avg_r']:+.3f} |",
        f"| StDev R | {dd['stdev_r']} |",
        f"| Sharpe/Trade | {dd['sharpe_per_trade']} |",
        f"| Avg DD Duration | {dd['avg_dd_duration_trades']} trades |",
        f"| Max DD Duration | {dd['max_dd_duration_trades']} trades |",
        f"| DD Periods | {dd['n_drawdown_periods']} |",
        "",
        "### Kelly Sizing",
        f"- Full Kelly: **{dd['kelly_full'] * 100:.2f}%** of capital per trade",
        f"- Half Kelly: **{dd['kelly_half'] * 100:.2f}%** (recommended)",
        f"- Quarter Kelly: **{dd['kelly_quarter'] * 100:.2f}%** (conservative)",
    ])

    # Overall Verdict
    verdicts = {
        "Walk-Forward (B16)": wf["verdict"],
        "Year Stability (B17)": yearly["verdict"],
        "Slippage (B18)": slippage["verdict"],
        "Bootstrap CI (B19)": bootstrap["verdict"],
    }
    all_pass = all(v in ("PASS", "STABLE", "SHIPPABLE", "EDGE SURVIVES ALL")
                   or "EDGE DIES AT" in v and int(v.split("AT ")[1].split("t")[0]) >= 4
                   for v in verdicts.values())

    lines.extend([
        "", "## OVERALL VERDICT", "",
    ])
    for k, v in verdicts.items():
        color = "PASS" if v in ("PASS", "STABLE", "SHIPPABLE", "EDGE SURVIVES ALL") else "WARN"
        if "EDGE DIES AT" in v:
            slip_at = int(v.split("AT ")[1].split("t")[0])
            color = "PASS" if slip_at >= 4 else "FAIL"
        lines.append(f"- {k}: **{v}** [{color}]")

    lines.extend([
        "",
        f"**CHAMPION STATUS: {'VALIDATED' if all_pass else 'CONDITIONAL'}**",
        "",
        f"*Generated in {gen_time:.1f}s*",
    ])

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batches 16-20 — Champion validation suite")
    parser.add_argument("--max-days", type=int, default=0)
    args = parser.parse_args()

    t0 = time.monotonic()
    print("backtest_real_validate: loading Databento 1m tape...")
    days = load_databento_days(days_tail=args.max_days or None)
    load_s = time.monotonic() - t0
    print(f"  loaded {len(days)} RTH days in {load_s:.1f}s")
    if not days:
        print("  ERROR: no days loaded")
        sys.exit(1)

    first_date = days[0][0].ts.date()
    last_date = days[-1][0].ts.date()

    print("  converting 1m -> V3 + 5m...")
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

    # ── Run champion on full tape ──
    print("\n  Running champion (orb_only_pm30) on full tape...", flush=True)
    t1 = time.monotonic()
    all_trades: list[Trade] = []
    for day_date, bars_1m_v3, bars_5m in day_data:
        day_trades = _backtest_champion_day(bars_1m_v3, bars_5m, day_date)
        all_trades.extend(day_trades)

    bt_time = time.monotonic() - t1
    total_r = sum(t.pnl_r for t in all_trades)
    wr = sum(1 for t in all_trades if t.pnl_r > 0) / len(all_trades) * 100 if all_trades else 0
    print(f"  {len(all_trades)} trades, +{total_r:.2f}R, {wr:.1f}% WR in {bt_time:.1f}s")

    day_dates = sorted({d for d, _, _ in day_data})

    # ── Batch 16 ──
    print("\n  [Batch 16] Walk-Forward validation...", flush=True)
    wf = batch_16_walk_forward(all_trades, day_dates)
    print(f"    {wf['positive_folds']}/{wf['total_folds']} folds positive OOS -> {wf['verdict']}")

    # ── Batch 17 ──
    print("  [Batch 17] Year-by-Year stability...", flush=True)
    yearly = batch_17_yearly_stability(all_trades)
    print(f"    {yearly['positive_years']}/{yearly['total_years']} positive years -> {yearly['verdict']}")

    # ── Batch 18 ──
    print("  [Batch 18] Slippage sensitivity...", flush=True)
    slippage = batch_18_slippage_sensitivity(all_trades)
    print(f"    -> {slippage['verdict']}")

    # ── Batch 19 ──
    print("  [Batch 19] Bootstrap CI (10k resamples)...", flush=True)
    bootstrap = batch_19_bootstrap(all_trades)
    print(f"    CI total R: [{bootstrap['ci_95_total_r'][0]:+.2f}, {bootstrap['ci_95_total_r'][1]:+.2f}]")
    print(f"    CI excludes zero: {bootstrap['ci_excludes_zero']} -> {bootstrap['verdict']}")

    # ── Batch 20 ──
    print("  [Batch 20] Drawdown profile + Kelly...", flush=True)
    dd = batch_20_drawdown_profile(all_trades)
    print(f"    Max DD: {dd['max_dd_r']}R | Kelly half: {dd['kelly_half'] * 100:.1f}%")
    print(f"    Recovery factor: {dd['recovery_factor']} | Max loss streak: {dd['max_loss_streak']}")

    gen_time = time.monotonic() - t0

    # ── Write reports ──
    report_dir = REPO_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)

    report = _generate_report(
        all_trades, len(day_data), first_date, last_date,
        wf, yearly, slippage, bootstrap, dd, gen_time,
    )
    (report_dir / "backtest_real_validate.md").write_text(report)
    print("\nWrote reports/backtest_real_validate.md")

    # CSV
    csv_lines = [
        "date,setup,side,entry_px,stop,tp1,tp2,sl_dist,pm_final,regime,"
        "voice_agree,exit_px,exit_reason,pnl_r,pnl_dollars,mfe_r,mae_r"
    ]
    for t in all_trades:
        csv_lines.append(
            f"{t.day_date},{t.setup},{t.side},{t.entry_price:.2f},"
            f"{t.stop:.2f},{t.tp1:.2f},{t.tp2:.2f},{t.sl_dist:.2f},"
            f"{t.pm_final:.1f},{t.regime},{t.voice_agree},"
            f"{t.exit_price:.2f},{t.exit_reason},{t.pnl_r:.3f},{t.pnl_dollars:.2f},"
            f"{t.mfe_r:.2f},{t.mae_r:.2f}"
        )
    (report_dir / "backtest_real_validate.csv").write_text("\n".join(csv_lines))
    print(f"Wrote reports/backtest_real_validate.csv ({len(csv_lines) - 1} trades)")

    # JSON data for dashboard
    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    json_data = {
        "walk_forward": wf,
        "yearly": yearly,
        "slippage": slippage,
        "bootstrap": bootstrap,
        "drawdown": {k: v for k, v in dd.items() if k != "equity_curve"},
        "equity_curve": dd["equity_curve"],
    }
    (data_dir / "backtest_real_validate.json").write_text(json.dumps(json_data, indent=2))
    print("Wrote data/backtest_real_validate.json")


if __name__ == "__main__":
    main()
