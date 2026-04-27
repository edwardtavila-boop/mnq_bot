"""Batch 15 — Ensemble signal combiner on real Databento MNQ tape.

Batches 12-14 established:
  - EMA 9/21 cross: NO EDGE (Batch 12)
  - V3 ORB @ PM40: +7.03R / 76% WR / PF 3.34 on 25 trades (Batch 13)
  - V3 ORB @ PM30: +29.89R / 76.1% WR on 109 trades (Batch 13)
  - V3 EMA PB: -9.70R drag at any PM threshold (Batch 13)
  - Micro refinement: hurts more than it helps (Batch 14)

This batch tests ensemble combinations:
  1. orb_only_pm30:     ORB setup only, suppress EMA PB entirely
  2. orb_only_pm40:     ORB only, higher quality gate
  3. orb_regime_pm30:   ORB only + regime filter (suppress CRISIS/RISK-OFF)
  4. orb_confidence:    ORB with PM-weighted sizing (higher PM → 100%, lower → 50%)
  5. orb_regime_conf:   ORB + regime + confidence — full ensemble
  6. all_setups_pm30:   All setups (control, matches Batch 13 v3_4)
  7. orb_sweep_pm30:    ORB + Sweep (no EMA PB)

Output:
    reports/backtest_real_ensemble.md
    reports/backtest_real_ensemble_trades.csv
    data/backtest_real_ensemble_daily.json

Usage:
    python scripts/backtest_real_ensemble.py
    python scripts/backtest_real_ensemble.py --max-days 200
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


def _aggregate_1m_to_5m(bars_1m: list[V3Bar]) -> list[V3Bar]:
    if not bars_1m:
        return []
    buckets: dict[int, list[V3Bar]] = {}
    for b in bars_1m:
        key = (b.time // 300) * 300
        buckets.setdefault(key, []).append(b)
    return [
        V3Bar(
            time=k,
            open=g[0].open,
            high=max(b.high for b in g),
            low=min(b.low for b in g),
            close=g[-1].close,
            volume=sum(b.volume for b in g),
        )
        for k, g in sorted(buckets.items())
    ]


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


def _resolve_exit_on_1m(
    *,
    side,
    entry_price,
    stop,
    tp1,
    tp2,
    sl_dist,
    use_partials,
    use_mfe_trail,
    trail_arm_R,
    trail_lock_R,
    bars_1m,
    signal_1m_ix,
    timeout_bars_1m,
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
            lock = (
                entry_price + sl_dist * trail_lock_R
                if side == "long"
                else entry_price - sl_dist * trail_lock_R
            )
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
            actual_r = (
                (current_sl - entry_price) / sl_dist
                if side == "long"
                else (entry_price - current_sl) / sl_dist
            )
            if tp1_filled:
                return current_sl, "tp1_then_be", max(actual_r, 0.5), bars_5m, mfe_r, mae_r
            return (
                current_sl,
                ("trail_lock" if actual_r > 0 else "stop"),
                actual_r,
                bars_5m,
                mfe_r,
                mae_r,
            )

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

    last = bars_1m[-1]
    return (
        last.close,
        "session_end",
        (0.5 if tp1_filled else 0.0),
        (len(bars_1m) - signal_1m_ix) // 5,
        mfe_r,
        mae_r,
    )


@dataclass
class EnsembleTrade:
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
    size_mult: float  # Confidence-based sizing
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_r: float = 0.0
    pnl_dollars: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0


@dataclass
class EnsembleConfig:
    name: str
    firm_cfg: FirmConfig
    det_cfg: V1DetectorConfig
    allowed_setups: set  # {"ORB", "EMA PB", "SWEEP"} — empty = all
    blocked_regimes: set  # {"CRISIS", "RISK-OFF"} — empty = none blocked
    use_confidence_sizing: bool = False
    confidence_pm_full: float = 50.0  # PM above this → 100% size
    confidence_pm_half: float = 35.0  # PM below this → 50% size


@dataclass
class EnsembleStats:
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
    trades: list[EnsembleTrade] = field(default_factory=list)
    by_setup: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)
    signals_total: int = 0
    signals_blocked_setup: int = 0
    signals_blocked_regime: int = 0
    days_traded: int = 0
    total_days: int = 0


ENSEMBLE_VARIANTS: list[EnsembleConfig] = [
    EnsembleConfig(
        name="orb_only_pm30",
        firm_cfg=FirmConfig(pm_threshold=30.0, require_setup=True),
        det_cfg=V1DetectorConfig(
            exit_mode="fibonacci",
            use_partials=True,
            entry_mode="pullback",
            ema_tod_filter="Power Hours",
            ema_dow_filter="All Days",
        ),
        allowed_setups={"ORB"},
        blocked_regimes=set(),
    ),
    EnsembleConfig(
        name="orb_only_pm40",
        firm_cfg=FirmConfig(pm_threshold=40.0, require_setup=True),
        det_cfg=V1DetectorConfig(
            exit_mode="fibonacci",
            use_partials=True,
            entry_mode="pullback",
            ema_tod_filter="Power Hours",
            ema_dow_filter="All Days",
        ),
        allowed_setups={"ORB"},
        blocked_regimes=set(),
    ),
    EnsembleConfig(
        name="orb_regime_pm30",
        firm_cfg=FirmConfig(pm_threshold=30.0, require_setup=True),
        det_cfg=V1DetectorConfig(
            exit_mode="fibonacci",
            use_partials=True,
            entry_mode="pullback",
            ema_tod_filter="Power Hours",
            ema_dow_filter="All Days",
        ),
        allowed_setups={"ORB"},
        blocked_regimes={"CRISIS", "RISK-OFF"},
    ),
    EnsembleConfig(
        name="orb_confidence_pm30",
        firm_cfg=FirmConfig(pm_threshold=30.0, require_setup=True),
        det_cfg=V1DetectorConfig(
            exit_mode="fibonacci",
            use_partials=True,
            entry_mode="pullback",
            ema_tod_filter="Power Hours",
            ema_dow_filter="All Days",
        ),
        allowed_setups={"ORB"},
        blocked_regimes=set(),
        use_confidence_sizing=True,
    ),
    EnsembleConfig(
        name="orb_regime_conf_pm30",
        firm_cfg=FirmConfig(pm_threshold=30.0, require_setup=True),
        det_cfg=V1DetectorConfig(
            exit_mode="fibonacci",
            use_partials=True,
            entry_mode="pullback",
            ema_tod_filter="Power Hours",
            ema_dow_filter="All Days",
        ),
        allowed_setups={"ORB"},
        blocked_regimes={"CRISIS", "RISK-OFF"},
        use_confidence_sizing=True,
    ),
    EnsembleConfig(
        name="all_setups_pm30",
        firm_cfg=FirmConfig(pm_threshold=30.0, require_setup=True),
        det_cfg=V1DetectorConfig(
            exit_mode="fibonacci",
            use_partials=True,
            entry_mode="pullback",
            ema_tod_filter="Power Hours",
            ema_dow_filter="All Days",
        ),
        allowed_setups=set(),  # All setups
        blocked_regimes=set(),
    ),
    EnsembleConfig(
        name="orb_sweep_pm30",
        firm_cfg=FirmConfig(pm_threshold=30.0, require_setup=True),
        det_cfg=V1DetectorConfig(
            exit_mode="fibonacci",
            use_partials=True,
            entry_mode="pullback",
            ema_tod_filter="Power Hours",
            ema_dow_filter="All Days",
        ),
        allowed_setups={"ORB", "SWEEP"},
        blocked_regimes=set(),
    ),
    EnsembleConfig(
        name="orb_regime_conf_pm25",
        firm_cfg=FirmConfig(pm_threshold=25.0, require_setup=True),
        det_cfg=V1DetectorConfig(
            exit_mode="fibonacci",
            use_partials=True,
            entry_mode="pullback",
            ema_tod_filter="Power Hours",
            ema_dow_filter="All Days",
        ),
        allowed_setups={"ORB"},
        blocked_regimes={"CRISIS"},
        use_confidence_sizing=True,
    ),
]


def _compute_sl_tp(setup, side, det_cfg, detector, bar, entry_price):
    atr = bar.atr or 1.0
    use_fib = det_cfg.exit_mode == "fibonacci" or (
        det_cfg.exit_mode == "hybrid" and setup in ("ORB", "SWEEP")
    )

    if setup == "ORB":
        or_low, or_high = detector.or_low, detector.or_high
        sl = (
            or_low - atr * 0.15
            if side == "long" and or_low is not None
            else or_high + atr * 0.15
            if side == "short" and or_high is not None
            else entry_price - atr * 1.5
            if side == "long"
            else entry_price + atr * 1.5
        )
        if (
            use_fib
            and or_low is not None
            and or_high is not None
            and (or_high - or_low) > atr * 0.5
        ):
            or_range = or_high - or_low
            tp1 = (
                or_high + or_range * (det_cfg.fib_tp1_extension - 1.0)
                if side == "long"
                else or_low - or_range * (det_cfg.fib_tp1_extension - 1.0)
            )
            tp2 = (
                or_high + or_range * (det_cfg.fib_tp2_extension - 1.0)
                if side == "long"
                else or_low - or_range * (det_cfg.fib_tp2_extension - 1.0)
            )
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
        sl = (
            entry_price - atr * det_cfg.ema_sl_atr
            if side == "long"
            else entry_price + atr * det_cfg.ema_sl_atr
        )
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
        sl = (
            swept_lo - buf
            if side == "long" and swept_lo is not None
            else swept_hi + buf
            if side == "short" and swept_hi is not None
            else entry_price - atr * 1.5
            if side == "long"
            else entry_price + atr * 1.5
        )
        if (
            use_fib
            and swept_lo is not None
            and swept_hi is not None
            and abs(swept_hi - swept_lo) > atr * 0.5
        ):
            sr = min(abs(swept_hi - swept_lo), atr * 3.0)
            tp1 = (
                entry_price + sr * det_cfg.fib_tp1_extension
                if side == "long"
                else entry_price - sr * det_cfg.fib_tp1_extension
            )
            tp2 = (
                entry_price + sr * det_cfg.fib_tp2_extension
                if side == "long"
                else entry_price - sr * det_cfg.fib_tp2_extension
            )
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


def _backtest_ensemble_day(
    ecfg: EnsembleConfig,
    bars_1m_v3: list[V3Bar],
    bars_5m: list[V3Bar],
    day_date: str,
) -> tuple[list[EnsembleTrade], int, int, int]:
    """Returns (trades, signals_total, blocked_setup, blocked_regime)."""
    if not bars_5m or len(bars_5m) < 10:
        return [], 0, 0, 0

    detector = V1Detector(cfg=ecfg.det_cfg)
    state = IndicatorState()
    trades: list[EnsembleTrade] = []
    cooldown_until = -1
    sig_total = sig_blocked_setup = sig_blocked_regime = 0

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
            cfg=ecfg.firm_cfg,
        )

        if not (d.fire_long or d.fire_short):
            continue
        if bar_ix < cooldown_until:
            continue

        sig_total += 1
        side = "long" if d.fire_long else "short"
        setup = d.setup_name or "FIRM"

        # Ensemble filter: allowed setups
        if ecfg.allowed_setups and setup not in ecfg.allowed_setups:
            sig_blocked_setup += 1
            continue

        # Ensemble filter: blocked regimes
        if regime in ecfg.blocked_regimes:
            sig_blocked_regime += 1
            continue

        # Confidence-based sizing
        size_mult = 1.0
        if ecfg.use_confidence_sizing:
            if d.pm_final >= ecfg.confidence_pm_full:
                size_mult = 1.0
            elif d.pm_final >= ecfg.confidence_pm_half:
                size_mult = 0.75
            else:
                size_mult = 0.5

        entry_price = bar.close
        sl, tp1, tp2, timeout = _compute_sl_tp(
            setup, side, ecfg.det_cfg, detector, bar, entry_price
        )
        sl_dist = abs(entry_price - sl)
        if sl_dist <= 0:
            continue

        signal_1m_ix = _find_1m_ix(bars_1m_v3, bar.time + 300)
        exit_px, exit_reason, pnl_r, _, mfe_r, mae_r = _resolve_exit_on_1m(
            side=side,
            entry_price=entry_price,
            stop=sl,
            tp1=tp1,
            tp2=tp2,
            sl_dist=sl_dist,
            use_partials=ecfg.det_cfg.use_partials,
            use_mfe_trail=ecfg.det_cfg.use_mfe_trail,
            trail_arm_R=ecfg.det_cfg.trail_arm_R,
            trail_lock_R=ecfg.det_cfg.trail_lock_R,
            bars_1m=bars_1m_v3,
            signal_1m_ix=signal_1m_ix,
            timeout_bars_1m=timeout * 5,
        )

        # Apply confidence sizing to R
        sized_pnl_r = pnl_r * size_mult
        pnl_dollars = sized_pnl_r * sl_dist * POINT_VALUE

        trades.append(
            EnsembleTrade(
                day_date=day_date,
                setup=setup,
                side=side,
                entry_price=entry_price,
                stop=sl,
                tp1=tp1,
                tp2=tp2,
                sl_dist=sl_dist,
                pm_final=d.pm_final,
                regime=regime,
                voice_agree=d.voice_agree,
                size_mult=size_mult,
                exit_price=exit_px,
                exit_reason=exit_reason,
                pnl_r=sized_pnl_r,
                pnl_dollars=pnl_dollars,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )
        )
        cooldown_until = bar_ix + ecfg.det_cfg.cooldown

    return trades, sig_total, sig_blocked_setup, sig_blocked_regime


def _compute_ensemble_stats(
    name: str,
    trades: list[EnsembleTrade],
    total_days: int,
    sig_total: int,
    sig_setup: int,
    sig_regime: int,
) -> EnsembleStats:
    vs = EnsembleStats(name=name, total_days=total_days)
    vs.total_trades = len(trades)
    vs.trades = trades
    vs.signals_total = sig_total
    vs.signals_blocked_setup = sig_setup
    vs.signals_blocked_regime = sig_regime

    day_trades: dict[str, list[EnsembleTrade]] = {}
    for t in trades:
        day_trades.setdefault(t.day_date, []).append(t)
    vs.days_traded = len(day_trades)

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

        vs.by_regime.setdefault(t.regime, {"trades": 0, "wins": 0, "total_r": 0.0})
        vs.by_regime[t.regime]["trades"] += 1
        vs.by_regime[t.regime]["total_r"] += t.pnl_r
        if t.pnl_r > 0:
            vs.by_regime[t.regime]["wins"] += 1

    for d in sorted(day_trades.keys()):
        vs.daily_pnls.append(sum(t.pnl_dollars for t in day_trades[d]))

    return vs


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch 15 — Ensemble real-tape backtest")
    parser.add_argument("--max-days", type=int, default=0)
    args = parser.parse_args()

    print("backtest_real_ensemble: loading Databento 1m tape...")
    t0 = time.monotonic()
    days = load_databento_days(days_tail=args.max_days or None)
    load_s = time.monotonic() - t0
    print(f"  loaded {len(days)} RTH days in {load_s:.1f}s")
    if not days:
        print("  ERROR: no days loaded")
        sys.exit(1)

    first_date = days[0][0].ts.date()
    last_date = days[-1][0].ts.date()

    print("  converting 1m → V3 + 5m...")
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

    all_stats: list[EnsembleStats] = []
    daily_pnl_data: dict[str, dict[str, float]] = {}

    for vi, ecfg in enumerate(ENSEMBLE_VARIANTS):
        print(f"\n  [{vi + 1}/{len(ENSEMBLE_VARIANTS)}] {ecfg.name}...", flush=True)
        t2 = time.monotonic()
        all_trades: list[EnsembleTrade] = []
        total_sig = total_blocked_setup = total_blocked_regime = 0

        for day_date, bars_1m_v3, bars_5m in day_data:
            day_trades, sig, bs, br = _backtest_ensemble_day(ecfg, bars_1m_v3, bars_5m, day_date)
            all_trades.extend(day_trades)
            total_sig += sig
            total_blocked_setup += bs
            total_blocked_regime += br

        vs = _compute_ensemble_stats(
            ecfg.name,
            all_trades,
            len(day_data),
            total_sig,
            total_blocked_setup,
            total_blocked_regime,
        )
        all_stats.append(vs)

        day_pnl_map: dict[str, float] = {d: 0.0 for d, _, _ in day_data}
        for t in all_trades:
            day_pnl_map[t.day_date] = day_pnl_map.get(t.day_date, 0.0) + t.pnl_dollars
        daily_pnl_data[ecfg.name] = day_pnl_map

        elapsed = time.monotonic() - t2
        wr = vs.winners / vs.total_trades * 100 if vs.total_trades else 0
        gw = sum(t.pnl_r for t in all_trades if t.pnl_r > 0)
        gl = abs(sum(t.pnl_r for t in all_trades if t.pnl_r < 0))
        pf = gw / gl if gl > 0 else float("inf")
        print(
            f"    {vs.total_trades} trades ({total_sig} signals, "
            f"{total_blocked_setup} blocked-setup, {total_blocked_regime} blocked-regime)"
        )
        print(
            f"    R={vs.total_pnl_r:+.2f}, ${vs.total_pnl_dollars:+.2f}, "
            f"WR {wr:.0f}%, PF {pf:.2f}, MDD {vs.max_drawdown_r:.2f}R, {elapsed:.1f}s"
        )
        for setup, st in vs.by_setup.items():
            setup_wr = st["wins"] / st["trades"] * 100 if st["trades"] else 0
            print(
                f"      {setup:8s}: {st['trades']:3d} trades  {setup_wr:5.1f}% win  {st['total_r']:+.2f}R"
            )

    # ─── Reports ───
    report_dir = REPO_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)

    lines = [
        f"# Batch 15 — Ensemble Combiner — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Ensemble signal filters on V3 engine, Databento MNQ tape.",
        f"**{len(day_data)} clean RTH days** ({first_date} → {last_date})",
        "",
        "## Variant Summary",
        "",
        "| Variant | Trades | Signals | Blocked | W | L | WR% | Total R | Avg R | PF | MaxDD R | $ PnL | Sharpe |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for vs in sorted(all_stats, key=lambda x: x.total_pnl_r, reverse=True):
        wr = vs.winners / vs.total_trades * 100 if vs.total_trades else 0
        avg_r = vs.total_pnl_r / vs.total_trades if vs.total_trades else 0
        gw = sum(t.pnl_r for t in vs.trades if t.pnl_r > 0)
        gl = abs(sum(t.pnl_r for t in vs.trades if t.pnl_r < 0))
        pf = gw / gl if gl > 0 else float("inf")
        blocked = vs.signals_blocked_setup + vs.signals_blocked_regime
        if len(vs.daily_pnls) > 1:
            mu = statistics.mean(vs.daily_pnls)
            sd = statistics.stdev(vs.daily_pnls)
            sharpe = (mu / sd * (252**0.5)) if sd > 0 else 0.0
        else:
            sharpe = 0.0
        pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
        lines.append(
            f"| {vs.name} | {vs.total_trades} | {vs.signals_total} | {blocked} "
            f"| {vs.winners} | {vs.losers} | {wr:.1f} | {vs.total_pnl_r:+.2f} | {avg_r:+.3f} "
            f"| {pf_str} | {vs.max_drawdown_r:.2f} | ${vs.total_pnl_dollars:+,.2f} | {sharpe:+.2f} |"
        )

    # Find the best variant
    best = sorted(all_stats, key=lambda x: x.total_pnl_r, reverse=True)[0] if all_stats else None

    # Deep dive on top 3
    for vs in sorted(all_stats, key=lambda x: x.total_pnl_r, reverse=True)[:3]:
        lines.extend(["", f"### {vs.name}", ""])
        lines.append(
            f"- **Trades:** {vs.total_trades} / {vs.signals_total} signals "
            f"({vs.signals_blocked_setup} blocked by setup, {vs.signals_blocked_regime} by regime)"
        )
        lines.append(f"- **Days traded:** {vs.days_traded} / {vs.total_days}")
        if vs.total_trades:
            avg_mfe = statistics.mean(t.mfe_r for t in vs.trades)
            avg_mae = statistics.mean(t.mae_r for t in vs.trades)
            lines.append(f"- **Avg MFE:** {avg_mfe:+.2f}R  |  Avg MAE: {avg_mae:+.2f}R")
            if any(t.size_mult != 1.0 for t in vs.trades):
                avg_size = statistics.mean(t.size_mult for t in vs.trades)
                lines.append(f"- **Avg size multiplier:** {avg_size:.2f}x")
        lines.extend(["", "  **By Setup:**"])
        for setup, st in sorted(vs.by_setup.items()):
            lines.append(
                f"  - {setup}: {st['trades']} trades, "
                f"{st['wins'] / st['trades'] * 100:.0f}% WR, {st['total_r']:+.2f}R"
            )
        lines.extend(["", "  **By Regime:**"])
        for regime, rs in sorted(vs.by_regime.items()):
            lines.append(
                f"  - {regime}: {rs['trades']} trades, "
                f"{rs['wins'] / rs['trades'] * 100:.0f}% WR, {rs['total_r']:+.2f}R"
            )

    # Verdict
    lines.extend(["", "## Verdict", ""])
    if best and best.total_trades > 0:
        wr = best.winners / best.total_trades * 100
        avg_r = best.total_pnl_r / best.total_trades
        gw = sum(t.pnl_r for t in best.trades if t.pnl_r > 0)
        gl = abs(sum(t.pnl_r for t in best.trades if t.pnl_r < 0))
        pf = gw / gl if gl > 0 else float("inf")
        lines.append(f"**BEST ENSEMBLE: {best.name}**")
        lines.append(
            f"- {best.total_trades} trades over {best.total_days} days ({best.days_traded} active)"
        )
        lines.append(f"- {wr:.0f}% WR, {best.total_pnl_r:+.2f}R total, PF {pf:.2f}")
        lines.append(f"- Max DD: {best.max_drawdown_r:.2f}R, ${best.total_pnl_dollars:+,.2f}")
        lines.append("")

        # Compare to all_setups control
        control = next((s for s in all_stats if s.name == "all_setups_pm30"), None)
        if control:
            lift_r = best.total_pnl_r - control.total_pnl_r
            lines.append(
                f"**vs. all_setups control:** {lift_r:+.2f}R lift "
                f"({best.total_trades} vs {control.total_trades} trades)"
            )

    lines.extend(["", "## Key Findings", ""])
    lines.append("1. **ORB is the only setup with edge** — EMA PB is consistently negative")
    lines.append("2. **Fibonacci exits + partials = essential** — R-multiple exits destroy edge")
    lines.append(
        "3. **PM threshold trades quality vs quantity** — PM30 gets 109 ORB trades at 76% WR"
    )
    lines.append("4. **Micro entry refinement doesn't help** — tighter stops get whipsawed")

    lines.append("")
    lines.append(f"*Generated in {time.monotonic() - t0:.1f}s*")

    (report_dir / "backtest_real_ensemble.md").write_text("\n".join(lines))
    print("\nWrote reports/backtest_real_ensemble.md")

    csv_lines = [
        "variant,date,setup,side,entry_px,stop,tp1,tp2,sl_dist,pm_final,regime,"
        "voice_agree,size_mult,exit_px,exit_reason,pnl_r,pnl_dollars,mfe_r,mae_r"
    ]
    for vs in all_stats:
        for t in vs.trades:
            csv_lines.append(
                f"{vs.name},{t.day_date},{t.setup},{t.side},{t.entry_price:.2f},"
                f"{t.stop:.2f},{t.tp1:.2f},{t.tp2:.2f},{t.sl_dist:.2f},"
                f"{t.pm_final:.1f},{t.regime},{t.voice_agree},{t.size_mult:.2f},"
                f"{t.exit_price:.2f},{t.exit_reason},{t.pnl_r:.3f},{t.pnl_dollars:.2f},"
                f"{t.mfe_r:.2f},{t.mae_r:.2f}"
            )
    (report_dir / "backtest_real_ensemble_trades.csv").write_text("\n".join(csv_lines))
    print(f"Wrote reports/backtest_real_ensemble_trades.csv ({len(csv_lines) - 1} trades)")

    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "backtest_real_ensemble_daily.json").write_text(
        json.dumps(daily_pnl_data, indent=2)
    )
    print("Wrote data/backtest_real_ensemble_daily.json")


if __name__ == "__main__":
    main()
