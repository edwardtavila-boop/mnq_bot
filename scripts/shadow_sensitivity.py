"""Shadow venue sensitivity sweep — slippage / latency / partial-fill impact.

Batch 6C. Runs the shadow trading pipeline across a matrix of execution
friction scenarios and reports how PnL, trade count, and risk metrics
degrade as slippage, latency, and partial fill probability increase.

Sweep dimensions:
  - Slippage ticks: [0, 0.5, 1, 2, 4]
  - Latency ms:     [0, 25, 50, 100, 250]
  - Partial fill %: [0%, 5%, 10%, 25%]

The sweep uses the existing ShadowVenue providers (FixedTickSlippage,
FixedLatency, StochasticPartialFill) to ensure consistency with the
production shadow venue.

Output: ``reports/shadow_sensitivity.md``
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from real_eta_driver import day_pm_output_from_real_apex  # noqa: E402
from strategy_ab import _load_real_days  # noqa: E402
from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

from mnq.eta_v3.gate import apex_gate  # noqa: E402
from mnq.core.types import OrderType, Side, Signal  # noqa: E402
from mnq.sim.layer2 import Layer2Engine  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402
from mnq.venues.shadow import (  # noqa: E402
    FixedLatency,
    FixedTickSlippage,
    FullFill,
    ShadowVenue,
    StochasticPartialFill,
    ZeroLatency,
    ZeroSlippage,
)

BASELINE_SPEC = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
DEFAULT_REPORT = REPO_ROOT / "reports" / "shadow_sensitivity.md"
VARIANTS = {cfg.name: cfg for cfg in _VARIANT_LIST}

# Sweep dimensions
SLIPPAGE_TICKS = [0, 0.5, 1, 2, 4]
LATENCY_MS = [0, 25, 50, 100, 250]
PARTIAL_FILL_PCT = [0.0, 0.05, 0.10, 0.25]


@dataclass
class ScenarioResult:
    """Result for one friction scenario."""

    slippage_ticks: float
    latency_ms: int
    partial_fill_prob: float
    total_pnl: float
    n_trades: int
    n_partial_fills: int
    n_rejections: int
    avg_slippage_cost: float  # avg $ slippage per trade
    max_drawdown: float
    avg_pnl_per_trade: float


def _build_venue(
    *,
    slip_ticks: float,
    lat_ms: int,
    partial_prob: float,
    seed: int = 0,
) -> ShadowVenue:
    """Construct a ShadowVenue with the given friction parameters."""
    import random

    if slip_ticks == 0:
        slippage = ZeroSlippage()
    else:
        slippage = FixedTickSlippage(
            tick_count=1,
            tick_size=Decimal(str(slip_ticks * 0.25)),
        )

    latency = ZeroLatency() if lat_ms == 0 else FixedLatency(ms=lat_ms)

    if partial_prob == 0.0:
        partial_fill = FullFill()
    else:
        partial_fill = StochasticPartialFill(
            partial_prob=partial_prob,
            min_fill_pct=0.5,
            _rng=random.Random(seed + 100),
        )

    return ShadowVenue(
        slippage=slippage,
        latency=latency,
        partial_fill=partial_fill,
    )


def _run_scenario(
    *,
    days: list,
    ledgers: list,
    pm_outputs: list,
    slip_ticks: float,
    lat_ms: int,
    partial_prob: float,
    seed: int = 0,
) -> ScenarioResult:
    """Run one friction scenario across all days."""
    venue = _build_venue(
        slip_ticks=slip_ticks,
        lat_ms=lat_ms,
        partial_prob=partial_prob,
        seed=seed,
    )

    total_pnl = 0.0
    n_trades = 0
    n_partial = 0
    n_reject = 0
    total_slippage_cost = 0.0
    equity_curve: list[float] = [0.0]

    for (_regime, bars), ledger, pm_out in zip(days, ledgers, pm_outputs, strict=True):
        dec = apex_gate(pm_out)
        action = dec["action"]
        if action == "skip":
            continue

        mult = float(dec["size_mult"])

        for tr in ledger.trades:
            eff_qty = max(1, int(round(tr.qty * mult)))

            trade_side = Side.LONG if tr.pnl_dollars >= 0 else Side.SHORT
            ref = bars[0].close
            if trade_side == Side.LONG:
                stop_px = ref - Decimal("4.00")
                tp_px = ref + Decimal("4.00")
            else:
                stop_px = ref + Decimal("4.00")
                tp_px = ref - Decimal("4.00")
            signal = Signal(
                side=trade_side,
                qty=eff_qty,
                ref_price=ref,
                stop=stop_px,
                take_profit=tp_px,
                order_type=OrderType.MARKET,
            )

            result = venue.place_order(
                signal,
                at_price=bars[0].close,
                at_ts=bars[0].ts,
            )

            if result.rejected:
                n_reject += 1
                continue

            if result.fill.is_partial:
                n_partial += 1

            # Compute PnL: original trade PnL scaled by fill ratio + sizing
            fill_ratio = result.fill.qty / eff_qty if eff_qty > 0 else 1.0
            scale = Decimal(str(fill_ratio)) * Decimal(str(mult))
            trade_pnl = float(tr.pnl_dollars * scale)

            # Subtract venue slippage cost (entry + exit, per contract)
            slip_cost = float(result.slippage_ticks) * 2 * result.fill.qty
            trade_pnl -= slip_cost
            total_slippage_cost += slip_cost

            total_pnl += trade_pnl
            n_trades += 1
            equity_curve.append(equity_curve[-1] + trade_pnl)

    # Max drawdown
    peak = 0.0
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = peak - eq
        max_dd = max(max_dd, dd)

    avg_slip = total_slippage_cost / n_trades if n_trades > 0 else 0.0
    avg_pnl = total_pnl / n_trades if n_trades > 0 else 0.0

    venue.close()

    return ScenarioResult(
        slippage_ticks=slip_ticks,
        latency_ms=lat_ms,
        partial_fill_prob=partial_prob,
        total_pnl=round(total_pnl, 2),
        n_trades=n_trades,
        n_partial_fills=n_partial,
        n_rejections=n_reject,
        avg_slippage_cost=round(avg_slip, 2),
        max_drawdown=round(max_dd, 2),
        avg_pnl_per_trade=round(avg_pnl, 2),
    )


def _run_sweep(
    *,
    filtered_name: str = "r5_real_wide_target",
    seed: int = 0,
) -> list[ScenarioResult]:
    """Run the full sensitivity sweep."""
    from strategy_v2 import ScriptedStrategyV2

    spec = load_spec(BASELINE_SPEC)
    days = _load_real_days(timeframe="1m")
    variant_cfg = VARIANTS[filtered_name]

    # Pre-compute ledgers and PM outputs (same across all friction scenarios)
    ledgers = []
    pm_outputs = []
    for _regime, bars in days:
        strat = ScriptedStrategyV2(spec, cfg=variant_cfg)
        engine = Layer2Engine(spec, strat, seed=seed)
        engine._rejection_p = 0.0
        ledgers.append(engine.run(bars))

        pm_out = day_pm_output_from_real_apex(bars)
        pm_outputs.append(pm_out)

    results = []

    # Primary sweep: slippage (holding latency=0, partial=0)
    print("--- Slippage sensitivity (latency=0, partial=0%) ---")
    for slip in SLIPPAGE_TICKS:
        r = _run_scenario(
            days=days,
            ledgers=ledgers,
            pm_outputs=pm_outputs,
            slip_ticks=slip,
            lat_ms=0,
            partial_prob=0.0,
            seed=seed,
        )
        results.append(r)
        print(f"  slip={slip:.1f}t → PnL=${r.total_pnl:+,.2f}, "
              f"trades={r.n_trades}, slip_cost=${r.avg_slippage_cost:.2f}/trade, "
              f"maxDD=${r.max_drawdown:.2f}")

    # Latency sweep (holding slippage=1, partial=0)
    print("\n--- Latency sensitivity (slippage=1t, partial=0%) ---")
    for lat in LATENCY_MS:
        if lat == 0:
            continue  # Already covered in slippage sweep
        r = _run_scenario(
            days=days,
            ledgers=ledgers,
            pm_outputs=pm_outputs,
            slip_ticks=1.0,
            lat_ms=lat,
            partial_prob=0.0,
            seed=seed,
        )
        results.append(r)
        print(f"  lat={lat}ms → PnL=${r.total_pnl:+,.2f}, "
              f"trades={r.n_trades}, maxDD=${r.max_drawdown:.2f}")

    # Partial fill sweep (holding slippage=1, latency=50)
    print("\n--- Partial fill sensitivity (slippage=1t, latency=50ms) ---")
    for pf in PARTIAL_FILL_PCT:
        if pf == 0.0:
            continue  # Already covered
        r = _run_scenario(
            days=days,
            ledgers=ledgers,
            pm_outputs=pm_outputs,
            slip_ticks=1.0,
            lat_ms=50,
            partial_prob=pf,
            seed=seed,
        )
        results.append(r)
        print(f"  partial={pf:.0%} → PnL=${r.total_pnl:+,.2f}, "
              f"trades={r.n_trades}, partials={r.n_partial_fills}, "
              f"maxDD=${r.max_drawdown:.2f}")

    # Worst-case combined: high slippage + high latency + high partial
    print("\n--- Combined worst case ---")
    r = _run_scenario(
        days=days,
        ledgers=ledgers,
        pm_outputs=pm_outputs,
        slip_ticks=4.0,
        lat_ms=250,
        partial_prob=0.25,
        seed=seed,
    )
    results.append(r)
    print(f"  worst → PnL=${r.total_pnl:+,.2f}, "
          f"trades={r.n_trades}, partials={r.n_partial_fills}, "
          f"maxDD=${r.max_drawdown:.2f}")

    return results


def _render(results: list[ScenarioResult]) -> str:
    """Render the sensitivity report."""
    lines = ["# Shadow Venue Sensitivity Sweep", ""]
    lines.append("Batch 6C. Tests how PnL degrades under increasing execution friction.")
    lines.append("Baseline: r5_real_wide_target variant, 15-day sample, real Apex V3 gate.")
    lines.append("")

    # Slippage table
    lines.append("## Slippage Sensitivity")
    lines.append("")
    lines.append("Latency = 0ms, Partial fill = 0%")
    lines.append("")
    lines.append("| Ticks | PnL | Trades | Avg Slip $/trade | Max DD | Avg PnL/trade |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    slip_results = [r for r in results if r.latency_ms == 0 and r.partial_fill_prob == 0.0]
    baseline_pnl = slip_results[0].total_pnl if slip_results else 0.0
    for r in slip_results:
        delta = r.total_pnl - baseline_pnl
        marker = " ★" if r.slippage_ticks == 0 else ""
        lines.append(
            f"| {r.slippage_ticks:.1f} | ${r.total_pnl:+,.2f} "
            f"({delta:+.2f}) | {r.n_trades} | ${r.avg_slippage_cost:.2f} | "
            f"${r.max_drawdown:.2f} | ${r.avg_pnl_per_trade:+,.2f}{marker} |"
        )

    # Latency table
    lines.append("")
    lines.append("## Latency Sensitivity")
    lines.append("")
    lines.append("Slippage = 1 tick, Partial fill = 0%")
    lines.append("")
    lines.append("| Latency ms | PnL | Trades | Max DD | Avg PnL/trade |")
    lines.append("|---:|---:|---:|---:|---:|")
    lat_results = [r for r in results if r.slippage_ticks == 1.0 and r.partial_fill_prob == 0.0]
    for r in lat_results:
        lines.append(
            f"| {r.latency_ms} | ${r.total_pnl:+,.2f} | {r.n_trades} | "
            f"${r.max_drawdown:.2f} | ${r.avg_pnl_per_trade:+,.2f} |"
        )

    # Partial fill table
    lines.append("")
    lines.append("## Partial Fill Sensitivity")
    lines.append("")
    lines.append("Slippage = 1 tick, Latency = 50ms")
    lines.append("")
    lines.append("| Partial % | PnL | Trades | Partials | Max DD | Avg PnL/trade |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    pf_results = [r for r in results if r.slippage_ticks == 1.0 and r.latency_ms == 50]
    for r in pf_results:
        lines.append(
            f"| {r.partial_fill_prob:.0%} | ${r.total_pnl:+,.2f} | {r.n_trades} | "
            f"{r.n_partial_fills} | ${r.max_drawdown:.2f} | ${r.avg_pnl_per_trade:+,.2f} |"
        )

    # Worst case
    lines.append("")
    lines.append("## Worst-Case Combined")
    lines.append("")
    worst = results[-1]
    lines.append(
        "| Scenario | PnL | Trades | Partials | Rejections | Max DD | Avg PnL/trade |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| 4t slip + 250ms + 25% partial | ${worst.total_pnl:+,.2f} | "
        f"{worst.n_trades} | {worst.n_partial_fills} | {worst.n_rejections} | "
        f"${worst.max_drawdown:.2f} | ${worst.avg_pnl_per_trade:+,.2f} |"
    )

    # Interpretation
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")

    if slip_results:
        zero_pnl = slip_results[0].total_pnl
        one_tick = next((r for r in slip_results if r.slippage_ticks == 1.0), None)
        if one_tick and zero_pnl != 0:
            pct_loss = (zero_pnl - one_tick.total_pnl) / abs(zero_pnl) * 100
            lines.append(
                f"1-tick slippage costs {pct_loss:.0f}% of edge vs zero-slippage baseline. "
            )
        elif one_tick:
            lines.append(
                f"1-tick slippage impact: ${one_tick.total_pnl - zero_pnl:+.2f} vs baseline. "
            )

    if worst.total_pnl < 0:
        lines.append(
            "Worst-case friction turns the strategy negative — the edge is thin "
            "enough that 4-tick slippage erodes it completely. Real-world "
            "execution must be 1-tick or better."
        )
    else:
        lines.append(
            "Even worst-case friction leaves the strategy profitable, though "
            "with degraded PnL. The edge appears robust to execution friction."
        )

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shadow venue sensitivity sweep.")
    parser.add_argument("--filtered", type=str, default="r5_real_wide_target")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    print("=== Shadow Venue Sensitivity Sweep (Batch 6C) ===")
    results = _run_sweep(filtered_name=args.filtered, seed=args.seed)
    md = _render(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(f"\n{md}")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
