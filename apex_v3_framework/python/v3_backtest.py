"""
V3 True Backtester
==================
Subclasses the V1 Backtester and overrides trade management to apply V3
asymmetric payoff rules DURING simulation, not as post-hoc projection.

This is the rigorous validation of the V3 simulated results. If true V3
matches projected V3 within ~15%, the V3 system is real.

V3 Management Rules (executed during simulation):
  - Tier-based size at open
  - Stall exit at bar 6 (MFE < 0.2R AND MAE > -0.4R)
  - Cut loss early at MAE -0.6R if MFE never exceeded 0.3R
  - Three-stage TP: 33% @ +0.7R (BE), 33% @ +1.5R, 33% trailed by 9 EMA
  - Aggressive trail after +0.5R MFE

Optional:
  - MTF context modifier (1h trend) — see mtf_context.py
  - Intermarket confluence — see intermarket integration

Usage:
  python v3_backtest.py /tmp/historical/nq_5m.csv --pm 25
  python v3_backtest.py /tmp/historical/nq_5m.csv --pm 25 --mtf /tmp/historical/nq_1h.csv
  python v3_backtest.py /tmp/historical/nq_5m.csv --pm 25 --es /tmp/historical/es_5m.csv
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, List

from firm_engine import FirmConfig, Bar
from backtest import Backtester, V1DetectorConfig, Trade, load_csv

ET = ZoneInfo("America/New_York")
TICK = 0.25


@dataclass
class V3Config:
    """V3 management parameters."""
    # Stall exit
    stall_bar: int = 6
    stall_max_mfe: float = 0.2
    stall_min_mae: float = -0.4

    # Early loss cut
    early_cut_mae: float = -0.6
    early_cut_max_mfe: float = 0.3

    # Three-stage TP
    tp1_R: float = 0.7        # First partial
    tp2_R: float = 1.5        # Second partial
    tp_partial_pct: float = 0.33  # Each stage size

    # Trail
    trail_arm_R: float = 0.5
    trail_lock_R: float = 0.3

    # Tier sizing
    tier1_size: float = 1.0
    tier2_size: float = 0.5
    tier3_size: float = 0.25
    drop_tier3: bool = False  # If True, skip Tier 3 entirely

    # Optional MTF / IM modifiers
    use_mtf: bool = False
    use_intermarket: bool = False
    mtf_alignment_size_boost: float = 1.25  # 25% size up if MTF aligned
    mtf_counter_size_penalty: float = 0.75  # 25% size down if MTF counter
    im_alignment_boost: float = 1.25
    im_divergence_penalty: float = 0.5  # half size if ES diverges


def tier_classify(open_time, setup, regime):
    """Classify into V3 tier. Returns (tier, base_size, reason)."""
    et = datetime.fromtimestamp(open_time, tz=timezone.utc).astimezone(ET)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    d = dow_names[et.weekday()]
    m = et.hour * 60 + et.minute

    # TIER 1: Premium V2 conditions
    if d in ("Thu", "Fri") and m >= 10*60+30 and (setup != "ORB" or regime == "RISK-ON"):
        return 1, 1.0, "Tier1 premium"
    # TIER 3: Speculative
    if d in ("Mon", "Tue") or m < 9*60+45 or (setup == "ORB" and regime == "NEUTRAL"):
        if d in ("Mon", "Tue"):
            return 3, 0.25, f"Tier3 weak day ({d})"
        if m < 9*60+45:
            return 3, 0.25, "Tier3 open 15min"
        return 3, 0.25, "Tier3 ORB Neutral"
    # TIER 2: Standard
    return 2, 0.5, "Tier2 standard"


@dataclass
class V3Trade(Trade):
    """Extended Trade with V3 staged-exit tracking."""
    tier: int = 2
    tier_reason: str = ""
    base_size_mult: float = 0.5
    mtf_modifier: float = 1.0
    im_modifier: float = 1.0
    stage1_filled: bool = False  # 33% at TP1
    stage2_filled: bool = False  # 33% at TP2
    runner_active: bool = False
    realized_r: float = 0.0  # cumulative realized R from partials
    runner_size: float = 0.34  # remaining size after 2 partials


class V3Backtester(Backtester):
    """V3 management backtester. Overrides _open_trade and _manage_open."""

    def __init__(self, *args, v3_cfg: Optional[V3Config] = None,
                 mtf_loader=None, im_loader=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.v3_cfg = v3_cfg or V3Config()
        self.mtf_loader = mtf_loader  # callable: ts -> trend_direction (-1/0/+1)
        self.im_loader = im_loader    # callable: ts -> ES direction (-1/0/+1)
        self.tier_counts = {1: 0, 2: 0, 3: 0, "skipped": 0}

    def _open_trade(self, bar, idx, d, st, size_mult: float = 1.0):
        """Override to apply V3 tier classification + size scaling."""
        side = "long" if d.fire_long else "short"
        setup = d.setup_name

        # Classify tier
        tier, base_size, tier_reason = tier_classify(bar.time, setup, d.regime)

        # Optional: drop Tier 3 entirely
        if self.v3_cfg.drop_tier3 and tier == 3:
            self.tier_counts["skipped"] += 1
            return

        self.tier_counts[tier] += 1

        # MTF modifier
        mtf_mod = 1.0
        if self.v3_cfg.use_mtf and self.mtf_loader is not None:
            mtf_dir = self.mtf_loader(bar.time)
            signal_dir = 1 if side == "long" else -1
            if mtf_dir == signal_dir:
                mtf_mod = self.v3_cfg.mtf_alignment_size_boost
            elif mtf_dir == -signal_dir:
                mtf_mod = self.v3_cfg.mtf_counter_size_penalty

        # Intermarket modifier
        im_mod = 1.0
        if self.v3_cfg.use_intermarket and self.im_loader is not None:
            es_dir = self.im_loader(bar.time)
            signal_dir = 1 if side == "long" else -1
            if es_dir == signal_dir:
                im_mod = self.v3_cfg.im_alignment_boost
            elif es_dir == -signal_dir:
                im_mod = self.v3_cfg.im_divergence_penalty

        final_size = base_size * mtf_mod * im_mod * size_mult

        # Compute SL/TP using parent class logic but capture for V3 trade
        # Use simplified per-setup SL/TP
        if setup == "ORB":
            st_or_low = getattr(self.detector, 'or_low', None)
            st_or_high = getattr(self.detector, 'or_high', None)
            if side == "long" and st_or_low is not None:
                sl = st_or_low - (bar.atr or 0) * 0.15
            elif side == "short" and st_or_high is not None:
                sl = st_or_high + (bar.atr or 0) * 0.15
            else:
                sl = bar.close - (bar.atr or 0) * 1.5 if side == "long" else bar.close + (bar.atr or 0) * 1.5
            tp1_r, tp2_r = 1.5, 3.0
        elif setup == "EMA PB":
            sl_dist = (bar.atr or 0) * 1.5
            sl = bar.close - sl_dist if side == "long" else bar.close + sl_dist
            tp1_r, tp2_r = 1.0, 2.0
        elif setup == "SWEEP":
            buf = 4 * TICK
            if side == "long" and self.detector.swept_lo_px is not None:
                sl = self.detector.swept_lo_px - buf
            elif side == "short" and self.detector.swept_hi_px is not None:
                sl = self.detector.swept_hi_px + buf
            else:
                sl = bar.close - (bar.atr or 0) * 1.5 if side == "long" else bar.close + (bar.atr or 0) * 1.5
            tp1_r, tp2_r = 1.0, 2.0
        else:
            sl = bar.close - (bar.atr or 0) * 1.5 if side == "long" else bar.close + (bar.atr or 0) * 1.5
            tp1_r, tp2_r = 1.0, 2.0

        sl_dist = abs(bar.close - sl)
        if sl_dist <= 0:
            return

        # V3 uses its own staged TPs (override the per-setup TP1/TP2)
        v3_tp1 = bar.close + sl_dist * self.v3_cfg.tp1_R if side == "long" else bar.close - sl_dist * self.v3_cfg.tp1_R
        v3_tp2 = bar.close + sl_dist * self.v3_cfg.tp2_R if side == "long" else bar.close - sl_dist * self.v3_cfg.tp2_R

        t = V3Trade(
            open_idx=idx, open_time=bar.time, setup=setup, side=side,
            entry=bar.close, sl=sl, tp1=v3_tp1, tp2=v3_tp2, sl_dist=sl_dist,
            pm_final=d.pm_final, quant=d.quant_total, red=d.red_team,
            voices=d.voices, regime=d.regime, size_pct=int(100 * final_size),
            tier=tier, tier_reason=tier_reason, base_size_mult=base_size,
            mtf_modifier=mtf_mod, im_modifier=im_mod,
        )
        self.daily_trade_count += 1
        self.open_trades.append(t)

    def _manage_open(self, bar, idx):
        """Override with V3 staged management."""
        cfg = self.v3_cfg
        still = []
        for t in self.open_trades:
            held = idx - t.open_idx
            if held < 1:
                still.append(t)
                continue

            # Update MFE/MAE
            if t.sl_dist > 0:
                if t.side == "long":
                    favorable = (bar.high - t.entry) / t.sl_dist
                    adverse = (bar.low - t.entry) / t.sl_dist
                else:
                    favorable = (t.entry - bar.low) / t.sl_dist
                    adverse = (t.entry - bar.high) / t.sl_dist
                if favorable > t.mfe_R:
                    t.mfe_R = favorable
                    t.mfe_bar = held
                if adverse < t.mae_R:
                    t.mae_R = adverse

            sf = t.size_pct / 100.0  # final tier-adjusted size
            closed = False
            close_reason = ""

            # Check SL hit (full remaining size)
            if t.side == "long":
                sl_hit = bar.low <= t.sl
                tp1_hit = bar.high >= t.tp1
                tp2_hit = bar.high >= t.tp2
            else:
                sl_hit = bar.high >= t.sl
                tp1_hit = bar.low <= t.tp1
                tp2_hit = bar.low <= t.tp2

            # ─── V3 STAGE 1: Take 33% at TP1 (+0.7R) ───
            if not t.stage1_filled and tp1_hit and not sl_hit:
                t.stage1_filled = True
                t.realized_r += cfg.tp1_R * cfg.tp_partial_pct
                # Move SL to BE on remainder
                t.sl = t.entry
                still.append(t)
                continue

            # ─── V3 STAGE 2: Take 33% at TP2 (+1.5R) ───
            if t.stage1_filled and not t.stage2_filled and tp2_hit:
                t.stage2_filled = True
                t.realized_r += cfg.tp2_R * cfg.tp_partial_pct
                t.runner_active = True
                # Trail tighter for runner
                if bar.ema9 is not None and bar.atr is not None:
                    new_sl = bar.ema9 - bar.atr * 0.15 if t.side == "long" else bar.ema9 + bar.atr * 0.15
                    t.sl = max(t.sl, new_sl) if t.side == "long" else min(t.sl, new_sl)
                still.append(t)
                continue

            # ─── V3 RUNNER MANAGEMENT (after stage 2) ───
            if t.runner_active:
                # Trail with 9 EMA
                if bar.ema9 is not None and bar.atr is not None:
                    new_sl = bar.ema9 - bar.atr * 0.15 if t.side == "long" else bar.ema9 + bar.atr * 0.15
                    t.sl = max(t.sl, new_sl) if t.side == "long" else min(t.sl, new_sl)

            # ─── V3 AGGRESSIVE TRAIL after +0.5R MFE (pre-stage 1) ───
            elif not t.stage1_filled and t.mfe_R >= cfg.trail_arm_R:
                lock = t.entry + (t.sl_dist * cfg.trail_lock_R) if t.side == "long" \
                       else t.entry - (t.sl_dist * cfg.trail_lock_R)
                t.sl = max(t.sl, lock) if t.side == "long" else min(t.sl, lock)

            # ─── V3 STALL EXIT at bar 6 ───
            if (held >= cfg.stall_bar and not t.stage1_filled
                    and abs(t.mfe_R) < cfg.stall_max_mfe
                    and t.mae_R > cfg.stall_min_mae):
                # Exit at small loss/gain based on current price
                exit_r = (bar.close - t.entry) / t.sl_dist if t.side == "long" \
                         else (t.entry - bar.close) / t.sl_dist
                t.pnl_r = (t.realized_r + exit_r * (1.0 - cfg.tp_partial_pct *
                          (1 if t.stage1_filled else 0) -
                          cfg.tp_partial_pct * (1 if t.stage2_filled else 0))) * sf
                t.outcome = "v3_stall_exit"
                close_reason = "stall"
                closed = True

            # ─── V3 CUT LOSS EARLY ───
            elif (not t.stage1_filled and t.mae_R <= cfg.early_cut_mae
                  and t.mfe_R < cfg.early_cut_max_mfe):
                t.pnl_r = cfg.early_cut_mae * sf
                t.outcome = "v3_cut_loss_early"
                close_reason = "early_cut"
                closed = True

            # ─── SL hit (bag whatever's realized + losing remainder) ───
            elif sl_hit:
                # Realized partials stay locked, remainder takes the SL hit
                remaining_size = 1.0 - cfg.tp_partial_pct * (1 if t.stage1_filled else 0) \
                                  - cfg.tp_partial_pct * (1 if t.stage2_filled else 0)
                # Calculate exit R from current SL (if BE, that's 0; if original, that's -1R)
                exit_r = (t.sl - t.entry) / t.sl_dist if t.side == "long" \
                         else (t.entry - t.sl) / t.sl_dist
                t.pnl_r = (t.realized_r + exit_r * remaining_size) * sf
                t.outcome = f"v3_sl_after_stage{1 if t.stage1_filled else 0}{2 if t.stage2_filled else ''}"
                close_reason = "sl"
                closed = True

            # ─── Timeout (rare with V3 management) ───
            elif held >= 25:  # absolute max bars
                exit_r = (bar.close - t.entry) / t.sl_dist if t.side == "long" \
                         else (t.entry - bar.close) / t.sl_dist
                remaining_size = 1.0 - cfg.tp_partial_pct * (1 if t.stage1_filled else 0) \
                                  - cfg.tp_partial_pct * (1 if t.stage2_filled else 0)
                t.pnl_r = (t.realized_r + exit_r * remaining_size) * sf
                t.outcome = "v3_timeout"
                close_reason = "timeout"
                closed = True

            if closed:
                t.bars_to_resolution = held
                t.close_idx = idx; t.close_time = bar.time; t.close_px = bar.close
                self.trades.append(t)
                self.daily_pnl_r += t.pnl_r
                cum_r = sum(tt.pnl_r for tt in self.trades)
                self.equity_curve.append((bar.time, round(cum_r, 2)))
            else:
                still.append(t)

        self.open_trades = still


def main():
    p = argparse.ArgumentParser(description="V3 True Backtester")
    p.add_argument("csv")
    p.add_argument("--pm", type=float, default=25.0)
    p.add_argument("--mtf", help="1h CSV for MTF context")
    p.add_argument("--es", help="ES 5m CSV for intermarket")
    p.add_argument("--drop-tier3", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    print(f"Loading {args.csv}...")
    bars = load_csv(args.csv)
    print(f"  {len(bars):,} bars")

    # Optional MTF loader
    mtf_loader = None
    if args.mtf:
        from mtf_context import build_mtf_loader
        mtf_loader = build_mtf_loader(args.mtf)
        print(f"  MTF context loaded from {args.mtf}")

    # Optional Intermarket loader
    im_loader = None
    if args.es:
        from intermarket import _load_simple
        es_data = _load_simple(args.es)
        # Build simple ES direction lookup: returns +1 if close > open, -1 if <, 0 if equal
        def es_dir_at(ts):
            d = es_data.get(ts)
            if d is None:
                return 0
            return 1 if d['close'] > d['open'] else -1 if d['close'] < d['open'] else 0
        im_loader = es_dir_at
        print(f"  Intermarket loaded from {args.es}")

    v3_cfg = V3Config(
        use_mtf=args.mtf is not None,
        use_intermarket=args.es is not None,
        drop_tier3=args.drop_tier3,
    )
    cfg = FirmConfig(pm_threshold=args.pm, require_setup=True)
    det_cfg = V1DetectorConfig()

    bt = V3Backtester(cfg=cfg, detector_cfg=det_cfg, v3_cfg=v3_cfg,
                       mtf_loader=mtf_loader, im_loader=im_loader)
    s = bt.run(bars)

    print(f"\n{'='*72}")
    print(f"V3 TRUE BACKTEST RESULTS")
    print(f"{'='*72}")
    print(f"Tier counts: T1={bt.tier_counts[1]}  T2={bt.tier_counts[2]}  T3={bt.tier_counts[3]}  Skipped={bt.tier_counts['skipped']}")

    if s.get('trades', 0) == 0:
        print("No trades.")
        return

    print(f"Trades:        {s['trades']}")
    print(f"Wins:          {s['wins']}  ({s['win_rate']}%)")
    print(f"Losses:        {s['losses']}")
    print(f"Breakevens:    {s.get('breakevens',0)}")
    print(f"Total R:       {s['total_r']:+.2f}")
    print(f"Avg R/trade:   {s['avg_r']:+.4f}")
    print(f"Profit factor: {s['profit_factor']}")
    print(f"Max DD:        {s['max_drawdown_r']}R")

    print(f"\n── By Setup ──")
    for setup, st in s['by_setup'].items():
        print(f"  {setup:8s}: {st['trades']:3d} trades, {st['win_rate']*100:5.1f}% win, total {st['total_r']:+.2f}R")

    # By tier
    print(f"\n── By Tier ──")
    by_tier = {}
    for t in bt.trades:
        by_tier.setdefault(t.tier, []).append(t)
    for tier, ts in sorted(by_tier.items()):
        wins = sum(1 for tt in ts if tt.pnl_r > 0)
        losses = sum(1 for tt in ts if tt.pnl_r < 0)
        bes = sum(1 for tt in ts if tt.pnl_r == 0)
        total = sum(tt.pnl_r for tt in ts)
        wr = wins / len(ts) * 100 if ts else 0
        print(f"  Tier {tier}: {len(ts):3d} trades  W:{wins} L:{losses} BE:{bes}  win {wr:.1f}%  R={total:+.2f}")

    # By exit reason
    print(f"\n── By Exit Reason ──")
    by_exit = {}
    for t in bt.trades:
        by_exit.setdefault(t.outcome, []).append(t)
    for exit_r, ts in sorted(by_exit.items(), key=lambda x: -sum(t.pnl_r for t in x[1])):
        total = sum(t.pnl_r for t in ts)
        print(f"  {exit_r:30s}: {len(ts):3d} trades  R={total:+.2f}")


if __name__ == "__main__":
    main()
