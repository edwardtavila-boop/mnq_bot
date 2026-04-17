"""End-to-end PnL report harness (synthetic MNQ bars).

Runs the baseline spec through:
    1. Spec load + hash verification
    2. Python generator render
    3. Layer-2 simulator over `n_days` synthetic trading days
    4. Gate-15 (turnover) with bootstrap CIs, run per CPCV-like fold
    5. Full ledger statistics, per-exit-reason and per-side breakdowns
    6. Bootstrap CI for total PnL and expectancy
    7. Writes a Markdown report under reports/.

No external data — regimes (trend/chop/range) are generated synthetically
with seeded RNG so the report is reproducible.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import random
import statistics
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from mnq.core.types import Bar, OrderType, Side, Signal  # noqa: E402
from mnq.gauntlet.gates.gate_turnover import TurnoverConfig, run_gate_15  # noqa: E402
from mnq.gauntlet.stats import Bootstrap  # noqa: E402
from mnq.generators.python_exec import render_python  # noqa: E402
from mnq.sim.layer2 import Layer2Engine, TradeLedger  # noqa: E402
from mnq.spec.hash import hash_spec  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"


@dataclass
class Regime:
    name: str
    drift_per_bar: float   # in MNQ points
    osc_amp: float         # ± points
    osc_period: int        # bars
    noise_std: float       # Gaussian noise stdev in points
    vol_base: int          # baseline contract volume


REGIMES: list[Regime] = [
    Regime("trend_up",   drift_per_bar=+0.25, osc_amp=1.5, osc_period=30, noise_std=0.4, vol_base=280),
    Regime("trend_down", drift_per_bar=-0.20, osc_amp=1.2, osc_period=25, noise_std=0.5, vol_base=260),
    Regime("chop",       drift_per_bar= 0.0,  osc_amp=2.5, osc_period=18, noise_std=0.8, vol_base=220),
    Regime("range_bound", drift_per_bar=0.0,  osc_amp=1.8, osc_period=40, noise_std=0.3, vol_base=200),
    Regime("high_vol",   drift_per_bar=+0.05, osc_amp=4.0, osc_period=12, noise_std=1.5, vol_base=350),
]


def _round_to_tick(x: float, tick: float = 0.25) -> Decimal:
    n = round(x / tick)
    return Decimal(str(n * tick))


def synth_day(
    day_ix: int,
    *,
    regime: Regime,
    base_price: float = 20_000.0,
    bars_per_day: int = 390,  # 6.5h of 1m bars (RTH)
    seed: int = 0,
) -> list[Bar]:
    """Build 1-minute RTH bars starting at 09:30 NY.

    In addition to the base regime we inject:
      - A "drive" burst inside the rth_open_drive window (bars 5..45, i.e.
        09:35–10:15 NY) biased in regime.drift_per_bar direction with ~2x
        volume; this gives the EMA crosser + rvol filter something to
        latch onto.
      - A secondary drive inside the afternoon window (bars 270..315, i.e.
        14:00–14:45 NY) in the opposite direction half the time.
    """
    rng = random.Random((day_ix + 1) * 7919 + seed)
    # NOTE: the base-class session/window logic compares `bar.ts.time()` to the
    # spec's window strings literally — it does NOT convert to the exchange tz.
    # So for the window check to fire, we synthesise bars whose UTC clock
    # already reads NY-local (09:30…). This is the same convention the
    # integration tests already follow.
    start = datetime(2026, 1, 5, 9, 30, tzinfo=UTC) + timedelta(days=day_ix)
    start_price = base_price + rng.uniform(-15, 15)
    out: list[Bar] = []
    p = start_price

    # Drive windows (inclusive bar index). Start minute -> bar i:
    #   rth_open_drive: 09:30-10:30 NY = bars 0..59
    #   afternoon:      14:00-15:55 NY = bars 270..384
    # We keep drives inside the windows but skip the first 30s blackout.
    # Each afternoon drive is a *reversal*: short pre-dip (to force an EMA
    # cross-below), then a sustained rally with volume expansion — this is
    # the pattern the baseline spec's `crosses_above within 3` looks for.
    drive_a_start, drive_a_end = 2, 35
    # Two reversal setups in the afternoon (each: 12-bar sharp dip + 25-bar rally).
    # The dip must be strong enough (~6+ MNQ points) to push EMA(9) below EMA(21).
    setups = [(270, 282, 282, 315), (325, 337, 337, 375)]  # (dipS, dipE, rallyS, rallyE)

    # Direction: 60% in regime direction, else flip (to create both longs/shorts)
    dir_a = 1 if regime.drift_per_bar >= 0 else -1
    if rng.random() < 0.4:
        dir_a *= -1
    # Afternoon setups: pick direction for each (long-biased 70%).
    setup_dirs = [1 if rng.random() < 0.7 else -1 for _ in setups]

    for i in range(bars_per_day):
        # Base motion
        drift = regime.drift_per_bar
        osc = regime.osc_amp * math.sin(2 * math.pi * (i % regime.osc_period) / regime.osc_period)
        osc_prev = (
            0.0 if i == 0
            else regime.osc_amp * math.sin(2 * math.pi * ((i - 1) % regime.osc_period) / regime.osc_period)
        )
        noise = rng.gauss(0.0, regime.noise_std)

        # Drive injections
        drive = 0.0
        vol_mult = 1.0
        if drive_a_start <= i <= drive_a_end:
            # Morning trending drive — modest so EMAs converge by afternoon
            span = drive_a_end - drive_a_start
            phase = (i - drive_a_start) / max(1, span)
            envelope = math.sin(math.pi * phase)
            drive = dir_a * 0.8 * envelope
            vol_mult = 1.5 + 0.4 * envelope
        else:
            # Afternoon reversal setups: sharp dip then rally
            for (dS, dE, rS, rE), sd in zip(setups, setup_dirs, strict=True):
                if dS <= i <= dE:
                    dip_dir = -sd
                    span = max(1, dE - dS)
                    phase = (i - dS) / span
                    envelope = math.sin(math.pi * phase)
                    drive = dip_dir * 2.8 * envelope  # strong enough to flip EMA
                    vol_mult = 1.2 + 0.3 * envelope
                    break
                elif rS < i <= rE:
                    span = max(1, rE - rS)
                    phase = (i - rS) / span
                    envelope = math.sin(math.pi * phase)
                    drive = sd * 2.2 * envelope
                    vol_mult = 2.0 + 0.8 * envelope
                    break

        target = p + drift + (osc - osc_prev) + noise + drive
        bar_span = max(1.0, abs(target - p) + abs(noise) + 0.5 + regime.noise_std)

        o = p
        c = target
        hi = max(o, c) + rng.uniform(0.25, 0.75) * bar_span
        lo = min(o, c) - rng.uniform(0.25, 0.75) * bar_span
        v = max(50, int(regime.vol_base * vol_mult + rng.gauss(0, regime.vol_base * 0.15)))

        ts = start + timedelta(minutes=i)
        bar = Bar(
            ts=ts,
            open=_round_to_tick(o),
            high=_round_to_tick(hi),
            low=_round_to_tick(lo),
            close=_round_to_tick(c),
            volume=v,
            timeframe_sec=60,
        )
        # Guarantee OHLC invariants against rounding artifacts
        oc_hi = max(bar.open, bar.close)
        oc_lo = min(bar.open, bar.close)
        if bar.high < oc_hi or bar.low > oc_lo:
            bar = Bar(
                ts=ts,
                open=bar.open,
                high=max(bar.high, oc_hi),
                low=min(bar.low, oc_lo),
                close=bar.close,
                volume=v,
                timeframe_sec=60,
            )
        out.append(bar)
        p = float(bar.close)
    return out


def build_strategy(spec: Any, tmp_dir: Path) -> Any:
    src = render_python(spec)
    p = tmp_dir / "generated_strategy.py"
    p.write_text(src)
    sp = importlib.util.spec_from_file_location("generated_strategy_pnl", p)
    mod = importlib.util.module_from_spec(sp)  # type: ignore[arg-type]
    sys.modules["generated_strategy_pnl"] = mod
    sp.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def run_day(spec: Any, mod: Any, bars: list[Bar], seed: int) -> TradeLedger:
    strat = mod.build(spec)
    engine = Layer2Engine(spec, strat, seed=seed)
    engine._rejection_p = 0.0  # deterministic
    return engine.run(bars)


# ---------------------------------------------------------------------------
# Scripted strategy used for the "full-pipeline" leg of the report.
#
# The baseline spec is intentionally quiet on a 1m bar stream (its HTF
# `rising for_bars 2` check cannot fire across a 5m bucket on 1m data). To
# demonstrate the ledger / gate-15 / breakdowns pipeline end-to-end we run a
# second leg with a scripted signal generator that emits realistic long/short
# signals during the same synthetic session windows.
# ---------------------------------------------------------------------------


class ScriptedStrategy:
    """Duck-type match for StrategyBase: `on_bar(bar) -> Signal | None`
    plus `update_position(int)`. Emits a signal on pre-computed bar indices
    using a simple rule:
        - Long inside the afternoon window on bars with rising 9-vs-21 EMA.
        - Short inside the afternoon window when falling.
    Uses its own internal EMA state so the report leg is self-contained.
    """

    def __init__(self, spec: Any, *, risk_ticks: int = 12, rr: float = 1.5) -> None:
        self.spec = spec
        self._tick = Decimal(str(spec.instrument.tick_size))
        self._risk_ticks = risk_ticks
        self._rr = rr
        self._position = 0
        self._bar_ix = -1
        # EMAs on close
        self._k9 = 2.0 / (9 + 1)
        self._k21 = 2.0 / (21 + 1)
        self._e9: float | None = None
        self._e21: float | None = None
        self._last_diff: float | None = None
        self._cooldown = 0

    def update_position(self, q: int) -> None:
        self._position = q
        if q == 0:
            self._cooldown = 3  # re-entry cooldown after flat

    def on_bar(self, bar: Bar) -> Signal | None:
        self._bar_ix += 1
        c = float(bar.close)
        self._e9 = c if self._e9 is None else (self._e9 + self._k9 * (c - self._e9))
        self._e21 = c if self._e21 is None else (self._e21 + self._k21 * (c - self._e21))
        diff = self._e9 - self._e21

        if self._cooldown > 0:
            self._cooldown -= 1
            self._last_diff = diff
            return None

        # Only fire inside the afternoon window (bars 270..375 by synth convention)
        # to mirror the spirit of the baseline spec's session filter.
        if not (270 <= self._bar_ix <= 375):
            self._last_diff = diff
            return None

        if self._position != 0:
            self._last_diff = diff
            return None

        prev = self._last_diff
        self._last_diff = diff

        if prev is None:
            return None

        # Cross-detection
        if prev <= 0 and diff > 0.3:
            return self._make_signal(bar, Side.LONG)
        if prev >= 0 and diff < -0.3:
            return self._make_signal(bar, Side.SHORT)
        return None

    def _make_signal(self, bar: Bar, side: Side) -> Signal:
        from mnq.core.types import quantize_to_tick
        ref = quantize_to_tick(bar.close, self._tick)
        risk_pts = self._tick * self._risk_ticks
        reward_pts = risk_pts * Decimal(str(self._rr))
        if side is Side.LONG:
            stop = quantize_to_tick(ref - risk_pts, self._tick)
            tp = quantize_to_tick(ref + reward_pts, self._tick)
        else:
            stop = quantize_to_tick(ref + risk_pts, self._tick)
            tp = quantize_to_tick(ref - reward_pts, self._tick)
        return Signal(
            side=side,
            qty=1,
            ref_price=ref,
            stop=stop,
            take_profit=tp,
            order_type=OrderType.MARKET,
            limit_offset_ticks=0,
            market_fallback_ms=500,
            time_stop_bars=20,
            spec_hash="",
            spec_semver="scripted",
        )


def run_day_scripted(spec: Any, bars: list[Bar], seed: int) -> TradeLedger:
    strat = ScriptedStrategy(spec)
    engine = Layer2Engine(spec, strat, seed=seed)  # type: ignore[arg-type]
    engine._rejection_p = 0.0
    return engine.run(bars)


@dataclass
class DayResult:
    day_ix: int
    regime: str
    n_bars: int
    ledger: TradeLedger

    @property
    def pnl(self) -> Decimal:
        return self.ledger.total_pnl_dollars - self.ledger.total_commission_dollars

    @property
    def gross_pnl(self) -> Decimal:
        return self.ledger.total_pnl_dollars

    @property
    def commission(self) -> Decimal:
        return self.ledger.total_commission_dollars


@dataclass
class LegReport:
    """A full results block for one strategy "leg" (real or scripted)."""
    label: str
    total_trades: int
    total_gross_pnl: Decimal
    total_commission: Decimal
    total_net_pnl: Decimal
    win_rate: float
    expectancy: Decimal
    exit_reason_breakdown: dict[str, dict[str, Decimal | int]]
    side_breakdown: dict[str, dict[str, Decimal | int]]
    per_regime: dict[str, dict[str, Any]] = field(default_factory=dict)
    pnl_bootstrap: dict[str, float] | None = None
    tpd_bootstrap: dict[str, Any] | None = None
    gate_15: dict[str, Any] = field(default_factory=dict)
    per_day: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Report:
    spec_hash: str
    n_days: int
    bars_per_day: int
    regime_counts: dict[str, int]
    legs: list[LegReport] = field(default_factory=list)


def _wrap_trades_as_path(ledger: TradeLedger) -> Any:
    """Build a duck-typed object with `.trades_df` (polars) for gate_15."""
    rows = []
    for t in ledger.trades:
        rows.append({"entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "pnl": float(t.pnl_dollars)})
    if not rows:
        df = pl.DataFrame({"entry_ts": [], "exit_ts": [], "pnl": []}, schema={"entry_ts": pl.Datetime, "exit_ts": pl.Datetime, "pnl": pl.Float64})
    else:
        df = pl.DataFrame(rows)

    class _P:
        trades_df = df

    return _P()


def _build_leg(
    *,
    label: str,
    spec: Any,
    schedule: list[Regime],
    results: list[DayResult],
    seed: int,
    cpcv_folds: int,
) -> LegReport:

    # Aggregate ledger
    agg = TradeLedger()
    for r in results:
        for t in r.ledger.trades:
            agg.add(t)

    total_gross = agg.total_pnl_dollars
    total_comm = agg.total_commission_dollars
    total_net = total_gross - total_comm
    wr = agg.win_rate()
    exp = agg.expectancy_dollars()
    by_exit = agg.breakdown_by_exit_reason()
    by_side = agg.breakdown_by_side()

    # Per-regime rollup
    per_regime: dict[str, dict[str, Any]] = {}
    for r in results:
        b = per_regime.setdefault(
            r.regime,
            {"n_days": 0, "n_trades": 0, "gross_pnl": Decimal(0), "net_pnl": Decimal(0), "commission": Decimal(0)},
        )
        b["n_days"] += 1
        b["n_trades"] += r.ledger.n_trades
        b["gross_pnl"] += r.gross_pnl
        b["net_pnl"] += r.pnl
        b["commission"] += r.commission

    # Bootstrap CI over per-day net PnL
    per_day_net = np.asarray([float(r.pnl) for r in results], dtype=np.float64)
    pnl_boot: dict[str, float] | None = None
    if len(per_day_net) >= 2:
        bs = Bootstrap(n_boot=2000, ci_level=0.95, seed=seed)
        br = bs.estimate(per_day_net, statistic=np.mean)
        pnl_boot = {
            "mean_per_day": float(np.mean(per_day_net)),
            "lo_95": float(br.lo),
            "hi_95": float(br.hi),
            "std_per_day": float(np.std(per_day_net, ddof=1)) if len(per_day_net) > 1 else 0.0,
        }

    # Gate 15: split trades into CPCV-like folds by day, one "path" per fold.
    paths: list[Any] = []
    if cpcv_folds > 0 and results:
        folds: list[list[DayResult]] = [[] for _ in range(cpcv_folds)]
        for i, r in enumerate(results):
            folds[i % cpcv_folds].append(r)
        for fold in folds:
            lg = TradeLedger()
            for dr in fold:
                for t in dr.ledger.trades:
                    lg.add(t)
            paths.append(_wrap_trades_as_path(lg))

    gate_result = run_gate_15(paths, config=TurnoverConfig(use_bootstrap_ci=True, n_boot=1000))
    gate_15: dict[str, Any] = {
        "passed": gate_result.passed,
        "metric_values": {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in gate_result.metric_values.items() if k != "per_path_rates"},
        "per_path_rates": [float(x) for x in gate_result.metric_values.get("per_path_rates", [])],
        "failure_reason": gate_result.failure_reason,
    }

    # Turnover bootstrap for whole sample
    if per_day_net.size > 0:
        tpd_arr = np.asarray([r.ledger.n_trades for r in results], dtype=np.float64)
        bs = Bootstrap(n_boot=2000, ci_level=0.95, seed=seed)
        tr = bs.estimate(tpd_arr, statistic=np.mean)
        tpd_boot: dict[str, Any] | None = {
            "mean_tpd": float(np.mean(tpd_arr)),
            "lo_95": float(tr.lo),
            "hi_95": float(tr.hi),
        }
    else:
        tpd_boot = None

    per_day_rows: list[dict[str, Any]] = []
    for r in results:
        per_day_rows.append({
            "day": r.day_ix,
            "regime": r.regime,
            "n_trades": r.ledger.n_trades,
            "gross_pnl": float(r.gross_pnl),
            "commission": float(r.commission),
            "net_pnl": float(r.pnl),
        })

    return LegReport(
        label=label,
        total_trades=agg.n_trades,
        total_gross_pnl=total_gross,
        total_commission=total_comm,
        total_net_pnl=total_net,
        win_rate=wr,
        expectancy=exp,
        exit_reason_breakdown=by_exit,
        side_breakdown=by_side,
        per_regime=per_regime,
        pnl_bootstrap=pnl_boot,
        tpd_bootstrap=tpd_boot,
        gate_15=gate_15,
        per_day=per_day_rows,
    )


def build_report(
    *,
    n_days: int = 25,
    bars_per_day: int = 390,
    seed: int = 42,
    cpcv_folds: int = 5,
) -> Report:
    spec = load_spec(BASELINE)
    spec_hash = hash_spec(spec)

    tmp = REPO_ROOT / ".pnl_tmp"
    tmp.mkdir(exist_ok=True)
    mod = build_strategy(spec, tmp)

    # Assign regimes round-robin with seeded shuffle
    regimes = list(REGIMES)
    rng = random.Random(seed)
    schedule = [regimes[(i + rng.randint(0, len(regimes) - 1)) % len(regimes)] for i in range(n_days)]
    regime_counts: dict[str, int] = {}
    for r in schedule:
        regime_counts[r.name] = regime_counts.get(r.name, 0) + 1

    # --- Leg A: baseline spec through the generator ---
    real_results: list[DayResult] = []
    for i, reg in enumerate(schedule):
        bars = synth_day(i, regime=reg, bars_per_day=bars_per_day, seed=seed)
        ledger = run_day(spec, mod, bars, seed=seed + i)
        real_results.append(DayResult(day_ix=i, regime=reg.name, n_bars=len(bars), ledger=ledger))

    leg_real = _build_leg(
        label="baseline spec (generated)",
        spec=spec,
        schedule=schedule,
        results=real_results,
        seed=seed,
        cpcv_folds=cpcv_folds,
    )

    # --- Leg B: scripted strategy on the same bars (pipeline demo) ---
    script_results: list[DayResult] = []
    for i, reg in enumerate(schedule):
        bars = synth_day(i, regime=reg, bars_per_day=bars_per_day, seed=seed)
        ledger = run_day_scripted(spec, bars, seed=seed + i)
        script_results.append(DayResult(day_ix=i, regime=reg.name, n_bars=len(bars), ledger=ledger))

    leg_scripted = _build_leg(
        label="scripted strategy (full pipeline demo)",
        spec=spec,
        schedule=schedule,
        results=script_results,
        seed=seed,
        cpcv_folds=cpcv_folds,
    )

    return Report(
        spec_hash=spec_hash,
        n_days=n_days,
        bars_per_day=bars_per_day,
        regime_counts=regime_counts,
        legs=[leg_real, leg_scripted],
    )


def _render_leg(leg: LegReport, lines: list[str]) -> None:
    lines.append(f"## Leg: {leg.label}")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total trades | {leg.total_trades} |")
    lines.append(f"| Gross PnL | ${float(leg.total_gross_pnl):,.2f} |")
    lines.append(f"| Commission | ${float(leg.total_commission):,.2f} |")
    lines.append(f"| **Net PnL** | **${float(leg.total_net_pnl):,.2f}** |")
    lines.append(f"| Win rate | {leg.win_rate:.1%} |")
    lines.append(f"| Expectancy / trade (net) | ${float(leg.expectancy):,.2f} |")
    if leg.pnl_bootstrap:
        pb = leg.pnl_bootstrap
        lines.append(
            f"| Per-day net PnL | ${pb['mean_per_day']:.2f} "
            f"(95% CI [{pb['lo_95']:.2f}, {pb['hi_95']:.2f}], σ={pb['std_per_day']:.2f}) |"
        )
    if leg.tpd_bootstrap:
        tb = leg.tpd_bootstrap
        lines.append(f"| Trades / day | {tb['mean_tpd']:.2f} (95% CI [{tb['lo_95']:.2f}, {tb['hi_95']:.2f}]) |")
    lines.append("")

    lines.append("### Gate 15 — Turnover (bootstrap CI)")
    lines.append("")
    lines.append(f"- passed: **{leg.gate_15.get('passed')}**")
    for k, v in leg.gate_15.get("metric_values", {}).items():
        lines.append(f"  - {k}: {v}")
    lines.append(f"- per-fold trades/day: {leg.gate_15.get('per_path_rates')}")
    if leg.gate_15.get("failure_reason"):
        lines.append(f"- failure_reason: {leg.gate_15['failure_reason']}")
    lines.append("")

    lines.append("### Breakdown by Exit Reason")
    lines.append("")
    if leg.exit_reason_breakdown:
        lines.append("| Exit | n | total PnL | avg PnL |")
        lines.append("|---|---:|---:|---:|")
        for k, b in sorted(leg.exit_reason_breakdown.items()):
            lines.append(f"| {k} | {int(b['n'])} | ${float(b['pnl']):,.2f} | ${float(b['pnl_avg']):,.2f} |")
    else:
        lines.append("_no trades_")
    lines.append("")

    lines.append("### Breakdown by Side")
    lines.append("")
    if leg.side_breakdown:
        lines.append("| Side | n | total PnL | avg PnL |")
        lines.append("|---|---:|---:|---:|")
        for k, b in sorted(leg.side_breakdown.items()):
            lines.append(f"| {k} | {int(b['n'])} | ${float(b['pnl']):,.2f} | ${float(b['pnl_avg']):,.2f} |")
    else:
        lines.append("_no trades_")
    lines.append("")

    lines.append("### Per-Regime Performance")
    lines.append("")
    lines.append("| Regime | days | trades | gross PnL | commission | net PnL |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for k in sorted(leg.per_regime):
        b = leg.per_regime[k]
        lines.append(
            f"| {k} | {b['n_days']} | {b['n_trades']} | "
            f"${float(b['gross_pnl']):,.2f} | ${float(b['commission']):,.2f} | ${float(b['net_pnl']):,.2f} |"
        )
    lines.append("")

    lines.append("<details><summary>Per-Day Ledger Summary</summary>")
    lines.append("")
    lines.append("| Day | Regime | Trades | Gross | Comm | Net |")
    lines.append("|---:|---|---:|---:|---:|---:|")
    for d in leg.per_day:
        lines.append(
            f"| {d['day']} | {d['regime']} | {d['n_trades']} | "
            f"${d['gross_pnl']:,.2f} | ${d['commission']:,.2f} | ${d['net_pnl']:,.2f} |"
        )
    lines.append("")
    lines.append("</details>")
    lines.append("")


def render_markdown(rpt: Report) -> str:
    lines: list[str] = []
    lines.append("# EVOLUTIONARY TRADING ALGO // End-to-End PnL Report")
    lines.append("")
    lines.append(f"- Generated: {datetime.now(UTC).isoformat()}")
    lines.append("- Spec: `specs/strategies/v0_1_baseline.yaml`")
    lines.append(f"- Spec content hash: `{rpt.spec_hash}`")
    lines.append(f"- Synthetic days: **{rpt.n_days}** × {rpt.bars_per_day} bars (1-minute)")
    rc = ", ".join(f"{k}={v}" for k, v in sorted(rpt.regime_counts.items()))
    lines.append(f"- Regime schedule: {rc}")
    lines.append("")
    lines.append(
        "This report has two legs. Leg A runs the baseline spec through the real "
        "spec→generator→sim-engine pipeline. Leg B substitutes a scripted strategy "
        "that fires deterministically inside the afternoon window, so the downstream "
        "ledger, gate-15, and PnL analytics have trades to operate on end-to-end."
    )
    lines.append("")
    for leg in rpt.legs:
        _render_leg(leg, lines)

    lines.append("## Interpretation Notes")
    lines.append("")
    lines.append(
        "- The **baseline spec** is intentionally unoptimized. Its entry requires "
        "`rising htf_trend for_bars 2` on a 5-minute HTF ring fed from 1-minute bars; "
        "because the HTFWrapper repeats the last-completed value within each 5-minute "
        "bucket, that condition is structurally hard to satisfy. Zero or very few "
        "fires on synthetic data is the expected behaviour — see the strategy "
        "rationale in the spec YAML (\"Designed to LOSE in noisy regimes via the "
        "rvol filter rather than win cleverly\")."
    )
    lines.append(
        "- The **scripted leg** exists to exercise the simulator, ledger, and gate-15 "
        "with realistic trade counts. It is NOT the production strategy."
    )
    lines.append(
        "- **Gate-15 (bootstrap CI)** now requires the entire 95 % CI of trades/day "
        "to sit within `[min_trades_per_day, max_trades_per_day]` — strictly more "
        "conservative than the old point-estimate check."
    )
    lines.append(
        "- **Synthetic data caveats**: regimes are parameterised bluntly, the "
        "simulator uses conservative adverse-first intrabar fills, and HTF vs 1-m "
        "timing is idealised. Do not interpret PnL here as a real-world edge estimate."
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=25)
    ap.add_argument("--bars-per-day", type=int, default=390)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--out", type=str, default="reports/pnl_report.md")
    args = ap.parse_args()

    rpt = build_report(
        n_days=args.days,
        bars_per_day=args.bars_per_day,
        seed=args.seed,
        cpcv_folds=args.folds,
    )
    md = render_markdown(rpt)
    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"wrote {out}")
    for leg in rpt.legs:
        print(
            f"  leg={leg.label!r:>42}  trades={leg.total_trades:>4}  "
            f"net=${float(leg.total_net_pnl):>10,.2f}  wr={leg.win_rate:.1%}  "
            f"gate15={leg.gate_15.get('passed')}"
        )
    return 0


# Silence unused helpers for linters
_ = statistics

if __name__ == "__main__":
    raise SystemExit(main())
