"""Batch 12 — Full real-tape backtest of V2 strategy variants on Databento MNQ.

Runs every real-data-calibrated variant (r0–r7, t0–t17, m5_*) through the full
2.4M-row Databento 1m tape (~1,700 RTH trading days, 2019-05 → 2026-04).

Unlike live_sim (which exercises the full execution stack with journal, venue,
slippage, risk checks), this is a **signal-quality** backtest: zero slippage,
zero commission, deterministic stop/TP resolution on bar OHLC. The goal is a
clean PnL distribution we can validate the gauntlet/OW system against.

Output:
    reports/backtest_real.md          — per-variant summary table + overall stats
    reports/backtest_real_trades.csv  — full trade log (variant, date, side, entry, exit, pnl, bars_held)
    data/backtest_real_daily.json     — per-day PnL per variant (for OW/gate revalidation)

Usage:
    python scripts/backtest_real.py
    python scripts/backtest_real.py --variants r5_real_wide_target t16_r5_long_only
    python scripts/backtest_real.py --max-days 200
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

UTC = timezone.utc  # noqa: UP017

# Backport datetime.UTC for Python < 3.11 (codebase targets 3.14).
if not hasattr(_dt, "UTC"):
    _dt.UTC = timezone.utc  # type: ignore[attr-defined]  # noqa: UP017
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from real_bars import load_databento_days  # noqa: E402
from strategy_v2 import VARIANTS, ScriptedStrategyV2, StrategyConfig  # noqa: E402

from mnq.core.types import Bar, Side, Signal  # noqa: E402

TICK = Decimal("0.25")
POINT_VALUE = Decimal("2.00")

# Only test real-data-calibrated variants (r*, t*, m5_*) — skip synthetic v2* variants.
REAL_VARIANTS = [v for v in VARIANTS if v.name.startswith(("r", "t", "m5_", "n"))]


def _scrub_day(bars: list[Bar]) -> list[Bar]:
    """Remove bad bars from a day.

    The Databento continuous-contract tape has garbage bars: settlement prices,
    zero/negative closes, inter-contract bleed. These corrupt stdev calculations
    and generate bogus signals.

    Rules:
    1. Drop bars with close <= 0
    2. Drop bars where close jumps > 3% from previous bar (roll/settlement artifact)
    3. Forward-fill gaps so bar indices remain dense
    """
    if not bars:
        return bars

    clean: list[Bar] = [bars[0]] if float(bars[0].close) > 0 else []
    for b in bars[1:]:
        c = float(b.close)
        if c <= 0:
            continue
        if clean:
            prev_c = float(clean[-1].close)
            if prev_c > 0 and abs(c - prev_c) / prev_c > 0.03:
                continue
        clean.append(b)
    return clean


@dataclass
class Trade:
    variant: str
    day_date: str
    side: str
    entry_bar_ix: int
    exit_bar_ix: int
    entry_price: Decimal
    exit_price: Decimal
    stop: Decimal
    take_profit: Decimal
    exit_reason: str
    pnl_ticks: float
    pnl_dollars: float
    bars_held: int


@dataclass
class VariantStats:
    name: str
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    scratches: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_equity: float = 0.0
    equity: float = 0.0
    daily_pnls: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    long_trades: int = 0
    short_trades: int = 0
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    days_traded: int = 0
    total_days: int = 0


def _resolve_exit(
    *,
    sig: Signal,
    bars: list[Bar],
    entry_bar_ix: int,
    time_stop_bars: int,
) -> tuple[int, Decimal, str]:
    """Walk forward from the bar after entry to find the first exit trigger.

    Returns (exit_bar_ix, exit_price, exit_reason).
    """
    for i in range(entry_bar_ix + 1, len(bars)):
        bar = bars[i]
        if sig.side is Side.LONG:
            # Check stop first (conservative: assume stop hit before TP on same bar)
            if bar.low <= sig.stop:
                return i, sig.stop, "stop"
            if bar.high >= sig.take_profit:
                return i, sig.take_profit, "take_profit"
        else:
            if bar.high >= sig.stop:
                return i, sig.stop, "stop"
            if bar.low <= sig.take_profit:
                return i, sig.take_profit, "take_profit"
        if i - entry_bar_ix >= time_stop_bars:
            return i, bar.close, "time_stop"
    # Session end — close at last bar's close.
    return len(bars) - 1, bars[-1].close, "session_end"


def _backtest_variant_day(
    cfg: StrategyConfig,
    bars: list[Bar],
    day_date: str,
    spec: object,
) -> list[Trade]:
    """Run one variant on one day's bars. Return list of trades."""
    strat = ScriptedStrategyV2(spec, cfg=cfg)
    trades: list[Trade] = []
    cooldown_until = -1

    for bar_ix, bar in enumerate(bars):
        if bar_ix < cooldown_until:
            strat.on_bar(bar)
            continue

        sig = strat.on_bar(bar)
        if sig is None:
            continue

        # Resolve exit
        exit_ix, exit_px, exit_reason = _resolve_exit(
            sig=sig,
            bars=bars,
            entry_bar_ix=bar_ix,
            time_stop_bars=cfg.time_stop_bars,
        )

        pnl_raw = (exit_px - sig.ref_price) if sig.side is Side.LONG else (sig.ref_price - exit_px)

        pnl_ticks = float(pnl_raw / TICK)
        pnl_dollars = float(pnl_raw * POINT_VALUE)

        trades.append(Trade(
            variant=cfg.name,
            day_date=day_date,
            side="long" if sig.side is Side.LONG else "short",
            entry_bar_ix=bar_ix,
            exit_bar_ix=exit_ix,
            entry_price=sig.ref_price,
            exit_price=exit_px,
            stop=sig.stop,
            take_profit=sig.take_profit,
            exit_reason=exit_reason,
            pnl_ticks=pnl_ticks,
            pnl_dollars=pnl_dollars,
            bars_held=exit_ix - bar_ix,
        ))

        # Report outcome and advance past exit
        strat.report_trade_outcome(pnl_dollars=Decimal(str(pnl_dollars)))
        strat.update_position(0)
        cooldown_until = exit_ix + 1

    return trades


def _compute_stats(name: str, trades: list[Trade], total_days: int) -> VariantStats:
    vs = VariantStats(name=name, total_days=total_days)

    # Group trades by day
    day_trades: dict[str, list[Trade]] = {}
    for t in trades:
        day_trades.setdefault(t.day_date, []).append(t)

    vs.total_trades = len(trades)
    vs.days_traded = len(day_trades)

    for t in trades:
        vs.total_pnl += t.pnl_dollars
        vs.equity += t.pnl_dollars
        vs.peak_equity = max(vs.peak_equity, vs.equity)
        dd = vs.peak_equity - vs.equity
        vs.max_drawdown = max(vs.max_drawdown, dd)

        if t.pnl_dollars > 0:
            vs.winners += 1
        elif t.pnl_dollars < 0:
            vs.losers += 1
        else:
            vs.scratches += 1

        if t.side == "long":
            vs.long_trades += 1
            vs.long_pnl += t.pnl_dollars
        else:
            vs.short_trades += 1
            vs.short_pnl += t.pnl_dollars

    # Compute daily PnLs (including $0 for no-trade days)
    all_dates = sorted({t.day_date for t in trades})
    for d in all_dates:
        day_pnl = sum(t.pnl_dollars for t in day_trades.get(d, []))
        vs.daily_pnls.append(day_pnl)

    vs.trades = trades
    return vs


def _build_spec():
    """Build a minimal spec object that ScriptedStrategyV2 needs."""

    @dataclass
    class Instrument:
        tick_size: float = 0.25

    @dataclass
    class Spec:
        instrument: Instrument = field(default_factory=Instrument)

    return Spec()


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-tape V2 backtest")
    parser.add_argument("--variants", nargs="*", help="Variant names to test (default: all real-data variants)")
    parser.add_argument("--max-days", type=int, default=0, help="Limit to last N days (0=all)")
    parser.add_argument("--timeframe", choices=["1m", "5m"], default="1m")
    args = parser.parse_args()

    print("backtest_real: loading Databento tape...")
    t0 = time.monotonic()
    days = load_databento_days(days_tail=args.max_days or None)
    load_s = time.monotonic() - t0
    print(f"  loaded {len(days)} RTH days in {load_s:.1f}s")
    if not days:
        print("  ERROR: no days loaded — check data path")
        sys.exit(1)

    print(f"  date range: {days[0][0].ts.date()} → {days[-1][0].ts.date()}")

    # Pick variants
    if args.variants:
        variants = [v for v in REAL_VARIANTS if v.name in args.variants]
        if not variants:
            print(f"  ERROR: no matching variants. Available: {[v.name for v in REAL_VARIANTS]}")
            sys.exit(1)
    else:
        variants = REAL_VARIANTS

    # Scrub bad bars (settlement artifacts, negative closes, roll jumps)
    print("  scrubbing bad bars...")
    clean_days: list[list[Bar]] = []
    dropped_days = 0
    dropped_bars = 0
    for day_bars in days:
        scrubbed = _scrub_day(day_bars)
        if len(scrubbed) < 350:  # need ~90% of bars for strategy to work
            dropped_days += 1
            continue
        dropped_bars += len(day_bars) - len(scrubbed)
        clean_days.append(scrubbed)
    print(f"  {len(clean_days)} clean days ({dropped_days} dropped, {dropped_bars} bad bars removed)")

    spec = _build_spec()
    all_stats: list[VariantStats] = []
    daily_pnl_data: dict[str, dict[str, float]] = {}  # variant -> {date: pnl}

    for vi, cfg in enumerate(variants):
        # Skip 5m variants when running 1m tape (and vice versa)
        if cfg.name.startswith("m5_") and args.timeframe != "5m":
            continue
        if not cfg.name.startswith("m5_") and args.timeframe == "5m":
            continue

        print(f"  [{vi + 1}/{len(variants)}] {cfg.name}...", end=" ", flush=True)
        t1 = time.monotonic()
        all_trades: list[Trade] = []

        for day_bars in clean_days:
            day_date = day_bars[0].ts.strftime("%Y-%m-%d")
            day_trades = _backtest_variant_day(cfg, day_bars, day_date, spec)
            all_trades.extend(day_trades)

        vs = _compute_stats(cfg.name, all_trades, total_days=len(days))
        all_stats.append(vs)

        # Daily PnL for gate revalidation
        day_pnl_map: dict[str, float] = {}
        for day_bars in clean_days:
            d = day_bars[0].ts.strftime("%Y-%m-%d")
            day_pnl_map[d] = 0.0
        for t in all_trades:
            day_pnl_map[t.day_date] = day_pnl_map.get(t.day_date, 0.0) + t.pnl_dollars
        daily_pnl_data[cfg.name] = day_pnl_map

        elapsed = time.monotonic() - t1
        wr = vs.winners / vs.total_trades * 100 if vs.total_trades else 0
        print(f"{vs.total_trades} trades, ${vs.total_pnl:+.2f}, WR {wr:.0f}%, {elapsed:.1f}s")

    # --- Write reports ---
    report_dir = REPO_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)

    # 1. Summary markdown
    lines = [
        f"# Real-Tape Backtest — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"Databento MNQ 1m tape: **{len(clean_days)} clean RTH days** (of {len(days)} raw, "
        f"{days[0][0].ts.date()} → {days[-1][0].ts.date()})",
        "",
        "Zero slippage, zero commission — pure signal quality test.",
        "",
        "## Variant Summary",
        "",
        "| Variant | Trades | W | L | WR% | Net PnL | Avg/Trade | MaxDD | Sharpe | Long PnL | Short PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for vs in sorted(all_stats, key=lambda x: x.total_pnl, reverse=True):
        wr = vs.winners / vs.total_trades * 100 if vs.total_trades else 0
        avg = vs.total_pnl / vs.total_trades if vs.total_trades else 0
        # Daily Sharpe
        if len(vs.daily_pnls) > 1:
            mu = statistics.mean(vs.daily_pnls)
            sd = statistics.stdev(vs.daily_pnls)
            sharpe = (mu / sd * (252 ** 0.5)) if sd > 0 else 0.0
        else:
            sharpe = 0.0
        lines.append(
            f"| {vs.name} | {vs.total_trades} | {vs.winners} | {vs.losers} "
            f"| {wr:.1f} | ${vs.total_pnl:+,.2f} | ${avg:+.2f} "
            f"| ${vs.max_drawdown:,.2f} | {sharpe:+.2f} "
            f"| ${vs.long_pnl:+,.2f} | ${vs.short_pnl:+,.2f} |"
        )

    # Top-3 deep dive
    top3 = sorted(all_stats, key=lambda x: x.total_pnl, reverse=True)[:3]
    for vs in top3:
        lines.append("")
        lines.append(f"### {vs.name}")
        lines.append("")
        lines.append(f"- **Trades:** {vs.total_trades} ({vs.long_trades}L / {vs.short_trades}S)")
        lines.append(f"- **Days traded:** {vs.days_traded} / {vs.total_days}")
        avg_per_day = vs.total_pnl / vs.days_traded if vs.days_traded else 0
        lines.append(f"- **Avg PnL/day traded:** ${avg_per_day:+.2f}")
        if vs.total_trades:
            avg_bars = statistics.mean(t.bars_held for t in vs.trades)
            lines.append(f"- **Avg bars held:** {avg_bars:.1f}")
            stops = sum(1 for t in vs.trades if t.exit_reason == "stop")
            tps = sum(1 for t in vs.trades if t.exit_reason == "take_profit")
            ts_ = sum(1 for t in vs.trades if t.exit_reason == "time_stop")
            se = sum(1 for t in vs.trades if t.exit_reason == "session_end")
            lines.append(f"- **Exits:** stop={stops}, TP={tps}, time={ts_}, session={se}")

    lines.append("")
    lines.append(f"*Generated in {time.monotonic() - t0:.1f}s*")

    (report_dir / "backtest_real.md").write_text("\n".join(lines))
    print("\nWrote reports/backtest_real.md")

    # 2. Trade log CSV
    csv_lines = ["variant,date,side,entry_ix,exit_ix,entry_px,exit_px,stop,tp,exit_reason,pnl_ticks,pnl_dollars,bars_held"]
    for vs in all_stats:
        for t in vs.trades:
            csv_lines.append(
                f"{t.variant},{t.day_date},{t.side},{t.entry_bar_ix},{t.exit_bar_ix},"
                f"{t.entry_price},{t.exit_price},{t.stop},{t.take_profit},"
                f"{t.exit_reason},{t.pnl_ticks:.2f},{t.pnl_dollars:.2f},{t.bars_held}"
            )
    (report_dir / "backtest_real_trades.csv").write_text("\n".join(csv_lines))
    print(f"Wrote reports/backtest_real_trades.csv ({len(csv_lines) - 1} trades)")

    # 3. Daily PnL JSON for gate revalidation
    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "backtest_real_daily.json").write_text(json.dumps(daily_pnl_data, indent=2))
    print("Wrote data/backtest_real_daily.json")


if __name__ == "__main__":
    main()
