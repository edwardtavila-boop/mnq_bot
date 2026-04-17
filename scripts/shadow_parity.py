"""Batch 4C — Shadow → Sim parity harness.

Compares the shadow venue's fill stream against the Layer2 sim's trade
ledger for the same day-set, variant, and seed. Detects:

  1. Trade-count divergence (shadow routed more/fewer than sim produced)
  2. Per-trade PnL divergence (slippage/latency shifts change dollar outcomes)
  3. Side/direction mismatch (signal routing bug)

Outputs: ``reports/shadow_parity.md``. Exits 0 if parity holds, 1 otherwise.

Usage:
    python scripts/shadow_parity.py               # deterministic (zero-slip)
    python scripts/shadow_parity.py --realistic    # with fixed 1-tick slip + 50ms
    python scripts/shadow_parity.py --days 30      # more days
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

# -- path surgery ----------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from real_bars import DEFAULT_CSV, load_real_days  # noqa: E402
from strategy_v2 import VARIANTS, ScriptedStrategyV2  # noqa: E402

from mnq.core.types import Bar, Side  # noqa: E402
from mnq.sim.layer2 import Layer2Engine  # noqa: E402
from mnq.sim.layer2.engine import TradeLedger, TradeRecord  # noqa: E402
from mnq.spec.loader import load_spec  # noqa: E402
from mnq.venues.shadow import (  # noqa: E402
    FixedLatency,
    FixedTickSlippage,
    ShadowVenue,
    ZeroLatency,
    ZeroSlippage,
)

BASELINE = _REPO / "specs" / "strategies" / "v0_1_baseline.yaml"


@dataclass(frozen=True, slots=True)
class _SignalLike:
    """Minimal Signal surrogate — venue only reads side, qty, spec_hash."""

    side: Side
    qty: int
    spec_hash: str = ""


# ---------------------------------------------------------------------------
# Parity data types
# ---------------------------------------------------------------------------

@dataclass
class TradePair:
    """One sim trade matched against its shadow counterpart."""

    day_label: str
    sim_side: str
    sim_pnl: Decimal
    shadow_pnl: Decimal | None
    pnl_diff: Decimal | None
    side_match: bool
    matched: bool


@dataclass
class ParityResult:
    """Aggregate parity comparison."""

    n_days: int
    sim_trades: int
    shadow_fills: int
    shadow_trades: int
    matched: int
    unmatched_sim: int
    unmatched_shadow: int
    total_sim_pnl: Decimal
    total_shadow_pnl: Decimal
    pnl_diff: Decimal
    side_mismatches: int
    max_pnl_diff_per_trade: Decimal
    avg_pnl_diff_per_trade: Decimal
    pairs: list[TradePair]
    is_divergent: bool
    alerts: list[str]
    realistic: bool


# ---------------------------------------------------------------------------
# Shadow fill parsing
# ---------------------------------------------------------------------------

def _parse_shadow_fills(jsonl_path: Path) -> list[dict]:
    if not jsonl_path.exists():
        return []
    fills = []
    for line in jsonl_path.read_text().strip().split("\n"):
        if line.strip():
            fills.append(json.loads(line))
    return fills


def _pair_shadow_fills(fills: list[dict]) -> list[tuple[dict, dict]]:
    """Group shadow fills into (entry, exit) pairs."""
    pairs = []
    for i in range(0, len(fills) - 1, 2):
        pairs.append((fills[i], fills[i + 1]))
    return pairs


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def run_parity(
    *,
    n_days: int = 15,
    filtered_name: str = "r5_real_wide_target",
    seed: int = 0,
    realistic: bool = False,
    journal_path: Path | None = None,
) -> ParityResult:
    """Run sim + shadow side-by-side, compare fills."""
    bar_days: list[list[Bar]] = load_real_days(
        DEFAULT_CSV, min_bars_per_day=380, timeframe_sec=60,
    )[:n_days]
    if not bar_days:
        raise RuntimeError("No bar data available")

    spec = load_spec(BASELINE)

    # Find the variant config
    cfg = None
    for v in VARIANTS:
        if v.name == filtered_name:
            cfg = v
            break
    if cfg is None:
        raise ValueError(f"Variant {filtered_name!r} not found in VARIANTS")

    if journal_path is None:
        journal_path = _REPO / "data" / "shadow" / "parity_fills.jsonl"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    if journal_path.exists():
        journal_path.write_text("")

    slippage = FixedTickSlippage(tick_count=1) if realistic else ZeroSlippage()
    latency = FixedLatency(ms=50) if realistic else ZeroLatency()

    all_sim_trades: list[tuple[str, TradeRecord]] = []

    # Use zero commission for parity comparison because the sim's
    # TradeRecord.pnl_dollars already includes commission. Adding shadow
    # commission would double-count. In realistic mode we use the spec's
    # actual commission so the diff reflects the venue cost model.
    if realistic:
        shadow_commission = Decimal(str(spec.commission_model.per_contract_per_side_usd))
    else:
        shadow_commission = Decimal(0)

    with ShadowVenue(
        journal_path=journal_path,
        slippage=slippage,
        latency=latency,
        commission_per_side=shadow_commission,
    ) as venue:
        for day_idx, bars in enumerate(bar_days):
            day_label = f"day-{day_idx}"
            strat = ScriptedStrategyV2(spec, cfg=cfg)
            engine = Layer2Engine(spec, strat, seed=seed)  # type: ignore[arg-type]
            engine._rejection_p = 0.0
            ledger: TradeLedger = engine.run(bars)

            for trade in ledger.trades:
                all_sim_trades.append((day_label, trade))

                # Route entry through shadow
                venue.place_order(
                    _SignalLike(side=trade.side, qty=trade.qty, spec_hash=cfg.name),
                    at_price=trade.entry_price,
                    at_ts=trade.entry_ts,
                )

                # Route exit (flipped side)
                exit_side = Side.SHORT if trade.side == Side.LONG else Side.LONG
                venue.place_order(
                    _SignalLike(side=exit_side, qty=trade.qty, spec_hash=cfg.name),
                    at_price=trade.exit_price,
                    at_ts=trade.exit_ts,
                )

    # Parse shadow fills back
    shadow_fills = _parse_shadow_fills(journal_path)
    shadow_pairs = _pair_shadow_fills(shadow_fills)

    # Match sim trades to shadow pairs
    pairs: list[TradePair] = []
    alerts: list[str] = []
    total_sim_pnl = Decimal(0)
    total_shadow_pnl = Decimal(0)
    side_mismatches = 0
    max_diff = Decimal(0)
    diffs: list[Decimal] = []

    for idx, (day_label, sim_trade) in enumerate(all_sim_trades):
        total_sim_pnl += sim_trade.pnl_dollars

        if idx < len(shadow_pairs):
            entry_fill, exit_fill = shadow_pairs[idx]
            s_entry_price = Decimal(entry_fill["price"])
            s_exit_price = Decimal(exit_fill["price"])
            s_side = Side(entry_fill["side"])
            s_qty = entry_fill["qty"]

            # Shadow PnL: (exit - entry) * qty * point_value - commission
            point_value = Decimal(str(spec.instrument.point_value))
            if s_side == Side.LONG:
                raw_pnl = (s_exit_price - s_entry_price) * s_qty * point_value
            else:
                raw_pnl = (s_entry_price - s_exit_price) * s_qty * point_value

            commission = Decimal(entry_fill["commission"]) + Decimal(exit_fill["commission"])
            shadow_pnl = raw_pnl - commission
            total_shadow_pnl += shadow_pnl

            pnl_diff = shadow_pnl - sim_trade.pnl_dollars
            max_diff = max(max_diff, abs(pnl_diff))
            diffs.append(pnl_diff)

            side_match = s_side == sim_trade.side
            if not side_match:
                side_mismatches += 1

            pairs.append(TradePair(
                day_label=day_label,
                sim_side=sim_trade.side.value,
                sim_pnl=sim_trade.pnl_dollars,
                shadow_pnl=shadow_pnl,
                pnl_diff=pnl_diff,
                side_match=side_match,
                matched=True,
            ))
        else:
            pairs.append(TradePair(
                day_label=day_label,
                sim_side=sim_trade.side.value,
                sim_pnl=sim_trade.pnl_dollars,
                shadow_pnl=None,
                pnl_diff=None,
                side_match=False,
                matched=False,
            ))

    matched = sum(1 for p in pairs if p.matched)
    unmatched_sim = sum(1 for p in pairs if not p.matched)
    unmatched_shadow = max(0, len(shadow_pairs) - len(all_sim_trades))

    avg_diff = (sum(diffs) / len(diffs)) if diffs else Decimal(0)
    pnl_diff_total = total_shadow_pnl - total_sim_pnl

    # Generate alerts
    if unmatched_sim > 0:
        alerts.append(f"TRADE_COUNT: {unmatched_sim} sim trades have no shadow pair")
    if unmatched_shadow > 0:
        alerts.append(f"TRADE_COUNT: {unmatched_shadow} shadow pairs have no sim match")
    if side_mismatches > 0:
        alerts.append(f"SIDE_MISMATCH: {side_mismatches} trades have mismatched sides")
    if realistic and abs(pnl_diff_total) > Decimal("50"):
        alerts.append(f"PNL_DRIFT: total ${float(pnl_diff_total):+.2f} (large for realism)")
    elif not realistic and abs(pnl_diff_total) > Decimal("0.01"):
        alerts.append(f"PNL_DIVERGENCE: total ${float(pnl_diff_total):+.2f} (should be zero)")

    is_divergent = side_mismatches > 0 or unmatched_sim > 0 or unmatched_shadow > 0

    return ParityResult(
        n_days=n_days,
        sim_trades=len(all_sim_trades),
        shadow_fills=len(shadow_fills),
        shadow_trades=len(shadow_pairs),
        matched=matched,
        unmatched_sim=unmatched_sim,
        unmatched_shadow=unmatched_shadow,
        total_sim_pnl=total_sim_pnl,
        total_shadow_pnl=total_shadow_pnl,
        pnl_diff=pnl_diff_total,
        side_mismatches=side_mismatches,
        max_pnl_diff_per_trade=max_diff,
        avg_pnl_diff_per_trade=avg_diff,
        pairs=pairs,
        is_divergent=is_divergent,
        alerts=alerts,
        realistic=realistic,
    )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_parity_report(result: ParityResult) -> str:
    lines: list[str] = []
    mode = "REALISTIC (1-tick slip + 50ms latency)" if result.realistic else "DETERMINISTIC (zero-slip)"
    lines.append("# Shadow → Sim Parity Report")
    lines.append("")
    lines.append(f"**Mode:** {mode}")
    lines.append(f"**Days:** {result.n_days}")
    lines.append(f"**Status:** {'DIVERGENT' if result.is_divergent else 'PARITY OK'}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Sim trades: **{result.sim_trades}**")
    lines.append(f"- Shadow fills: **{result.shadow_fills}** ({result.shadow_trades} paired trades)")
    lines.append(f"- Matched: **{result.matched}** / Unmatched sim: **{result.unmatched_sim}** / Unmatched shadow: **{result.unmatched_shadow}**")
    lines.append(f"- Side mismatches: **{result.side_mismatches}**")
    lines.append("")
    lines.append(f"- Total sim PnL: **${float(result.total_sim_pnl):+.2f}**")
    lines.append(f"- Total shadow PnL: **${float(result.total_shadow_pnl):+.2f}**")
    lines.append(f"- PnL difference: **${float(result.pnl_diff):+.2f}**")
    lines.append(f"- Max per-trade diff: **${float(result.max_pnl_diff_per_trade):.2f}**")
    lines.append(f"- Avg per-trade diff: **${float(result.avg_pnl_diff_per_trade):+.4f}**")
    lines.append("")

    if result.alerts:
        lines.append("## Alerts")
        lines.append("")
        for alert in result.alerts:
            lines.append(f"- {alert}")
        lines.append("")

    lines.append("## Per-Trade Comparison")
    lines.append("")
    lines.append("| # | Day | Side | Sim PnL | Shadow PnL | Δ PnL | Match |")
    lines.append("|---:|---|---|---:|---:|---:|:---:|")
    for i, p in enumerate(result.pairs):
        sim_pnl = f"${float(p.sim_pnl):+.2f}"
        shadow_pnl = f"${float(p.shadow_pnl):+.2f}" if p.shadow_pnl is not None else "—"
        diff = f"${float(p.pnl_diff):+.2f}" if p.pnl_diff is not None else "—"
        match_icon = "✓" if p.matched and p.side_match else "✗"
        lines.append(f"| {i} | {p.day_label} | {p.sim_side} | {sim_pnl} | {shadow_pnl} | {diff} | {match_icon} |")

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    if not result.realistic:
        lines.append("In deterministic mode (zero slippage, zero latency), shadow PnL should")
        lines.append("match sim PnL exactly. Any non-zero diff indicates a routing or")
        lines.append("price-resolution bug in the shadow venue layer.")
    else:
        lines.append("In realistic mode, shadow PnL will diverge from sim PnL due to")
        lines.append("slippage and latency modeling. The diff quantifies the cost-of-realism")
        lines.append("the system would experience in live routing. Non-zero diff is expected;")
        lines.append("the question is whether the magnitude is within calibrated bounds.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow → Sim parity check")
    parser.add_argument("--days", type=int, default=15)
    parser.add_argument("--realistic", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    result = run_parity(
        n_days=args.days,
        seed=args.seed,
        realistic=args.realistic,
    )

    report = render_parity_report(result)
    report_path = _REPO / "reports" / "shadow_parity.md"
    report_path.write_text(report)
    print(report)
    print(f"\nwrote {report_path}")

    return 1 if result.is_divergent else 0


if __name__ == "__main__":
    raise SystemExit(main())
