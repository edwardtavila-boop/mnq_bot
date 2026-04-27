"""Paper-vs-live parity dashboard.

Compares realized PnL distributions across two environments (paper-sim and live/shadow)
to detect venue-side microstructure bugs. Uses paired bootstrap for statistical testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import numpy as np

from mnq.core.types import Side
from mnq.gauntlet.stats import BootstrapResult, paired_bootstrap
from mnq.storage.journal import EventJournal
from mnq.storage.schema import FILL_REALIZED


@dataclass(frozen=True)
class TradeSummary:
    """One closed trade's terminal record."""

    trace_id: str | None  # joins across envs if same
    symbol: str
    side: Side
    entry_ts: datetime
    exit_ts: datetime
    entry_price: Decimal
    exit_price: Decimal
    qty: int
    pnl_dollars: Decimal
    commission_dollars: Decimal
    exit_reason: str


@dataclass(frozen=True)
class EnvSummary:
    """Summary of one environment's trade stream."""

    env: str  # "paper" | "live" | "shadow"
    trades: list[TradeSummary]
    total_pnl: Decimal
    n_trades: int
    win_rate: float
    expectancy_dollars: Decimal
    avg_slippage_ticks: float
    n_rejected: int


@dataclass(frozen=True)
class ParityReport:
    """Result of comparing paper vs live."""

    paper: EnvSummary
    live: EnvSummary
    trade_pnl_diff_ci: BootstrapResult  # paired bootstrap of per-trade PnL diffs
    expectancy_diff_dollars: float
    win_rate_diff: float
    slippage_diff_ticks: float
    alerts: list[str]
    is_divergent: bool


def summarize_env(
    journal: EventJournal,
    *,
    env_label: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> EnvSummary:
    """Replay the journal, extract completed trades, build an EnvSummary.

    Convention: Trades are reconstructed from FILL_REALIZED events, which provide
    complete trade closure records with entry/exit prices, PnL, and commission.
    These events are written by the slippage recorder and include the full
    trade-level summary.

    Args:
        journal: EventJournal to replay.
        env_label: Label for this environment ("paper", "live", "shadow", etc.).
        since: Only include trades with entry_ts >= this datetime.
        until: Only include trades with exit_ts <= this datetime.

    Returns:
        EnvSummary with aggregated trade statistics.
    """
    trades: list[TradeSummary] = []
    slippage_values: list[float] = []
    rejected_count = 0

    # Replay FILL_REALIZED events to reconstruct closed trades
    for entry in journal.replay(event_types=(FILL_REALIZED,)):
        payload = entry.payload
        try:
            # Extract fields from FILL_REALIZED payload
            symbol: str = payload.get("symbol", "MNQ")
            side_str: str = payload.get("side", "long")
            side = Side(side_str) if isinstance(side_str, str) else Side.LONG
            entry_ts = datetime.fromisoformat(payload["entry_ts"])
            exit_ts = datetime.fromisoformat(payload["exit_ts"])
            entry_price = Decimal(str(payload.get("entry_price", "0")))
            exit_price = Decimal(str(payload.get("exit_price", "0")))
            qty: int = int(payload.get("qty", 1))
            pnl_dollars = Decimal(str(payload.get("pnl_dollars", "0")))
            commission_dollars = Decimal(str(payload.get("commission_dollars", "0")))
            exit_reason: str = payload.get("exit_reason", "unknown")
            slippage_ticks: float = float(payload.get("slippage_ticks", 0.0))

            # Apply time bounds
            if since is not None and entry_ts < since:
                continue
            if until is not None and exit_ts > until:
                continue

            trade = TradeSummary(
                trace_id=entry.trace_id,
                symbol=symbol,
                side=side,
                entry_ts=entry_ts,
                exit_ts=exit_ts,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                pnl_dollars=pnl_dollars,
                commission_dollars=commission_dollars,
                exit_reason=exit_reason,
            )
            trades.append(trade)
            slippage_values.append(slippage_ticks)

        except (KeyError, ValueError, TypeError):
            # Malformed event; skip
            rejected_count += 1
            continue

    # Compute aggregate stats
    total_pnl: Decimal = sum((t.pnl_dollars for t in trades), Decimal("0"))
    n_trades = len(trades)

    # Win rate: % of trades with pnl > 0 (exclude scratches with pnl == 0)
    winning_trades = sum(1 for t in trades if t.pnl_dollars > 0)
    losing_trades = sum(1 for t in trades if t.pnl_dollars < 0)
    decisive_trades = winning_trades + losing_trades
    win_rate = float(winning_trades) / float(decisive_trades) if decisive_trades > 0 else 0.0

    # Expectancy: avg PnL per trade
    expectancy_dollars: Decimal = total_pnl / Decimal(n_trades) if n_trades > 0 else Decimal("0")

    # Avg slippage
    avg_slippage_ticks = float(np.mean(slippage_values)) if slippage_values else 0.0

    return EnvSummary(
        env=env_label,
        trades=trades,
        total_pnl=total_pnl,
        n_trades=n_trades,
        win_rate=win_rate,
        expectancy_dollars=expectancy_dollars,
        avg_slippage_ticks=avg_slippage_ticks,
        n_rejected=rejected_count,
    )


def compare_envs(
    paper: EnvSummary,
    live: EnvSummary,
    *,
    divergence_threshold_dollars: float = 5.0,
    divergence_threshold_wr: float = 0.05,
    divergence_threshold_slip: float = 0.5,
    n_boot: int = 1000,
) -> ParityReport:
    """Run the comparison between two environments.

    Alerts generated when:
      1. Paired per-trade PnL CI's lower bound excludes 0 and |diff| >
         divergence_threshold_dollars
      2. Win-rate delta > divergence_threshold_wr
      3. Avg slippage delta > divergence_threshold_slip ticks

    is_divergent is True if any alert fires.

    Args:
        paper: EnvSummary for paper trading.
        live: EnvSummary for live/shadow trading.
        divergence_threshold_dollars: Min per-trade PnL diff to alert (default 5.0).
        divergence_threshold_wr: Min win-rate delta to alert (default 0.05).
        divergence_threshold_slip: Min slippage delta to alert (default 0.5 ticks).
        n_boot: Number of bootstrap resamples (default 1000).

    Returns:
        ParityReport with comparison results and alerts.
    """
    alerts: list[str] = []

    # Paired bootstrap on per-trade PnL diffs
    # Match trades by index if same count, otherwise use only common subset
    n_paper = len(paper.trades)
    n_live = len(live.trades)
    min_trades = min(n_paper, n_live)

    if min_trades > 0:
        paper_pnls = np.array(
            [float(paper.trades[i].pnl_dollars) for i in range(min_trades)],
            dtype=np.float64,
        )
        live_pnls = np.array(
            [float(live.trades[i].pnl_dollars) for i in range(min_trades)],
            dtype=np.float64,
        )

        # Compute live - paper (positive means live is better)
        trade_pnl_diff_ci = paired_bootstrap(
            paper_pnls,
            live_pnls,
            statistic=lambda p, live: float(np.mean(live - p)),
            n_boot=n_boot,
            ci_level=0.95,
            seed=42,
        )

        # Alert if CI excludes zero and diff is large
        if (trade_pnl_diff_ci.lo > 0 or trade_pnl_diff_ci.hi < 0) and abs(
            trade_pnl_diff_ci.point
        ) > divergence_threshold_dollars:
            direction = "better" if trade_pnl_diff_ci.point > 0 else "worse"
            alerts.append(
                f"PnL divergence: live is ${abs(trade_pnl_diff_ci.point):.2f}/trade "
                f"{direction} (CI: ${trade_pnl_diff_ci.lo:.2f}–${trade_pnl_diff_ci.hi:.2f})"
            )
    else:
        # No paired trades; use zero CI
        trade_pnl_diff_ci = BootstrapResult(
            point=0.0,
            lo=0.0,
            hi=0.0,
            n=0,
            ci_level=0.95,
            n_boot=n_boot,
        )

    # Win-rate delta
    wr_diff = live.win_rate - paper.win_rate
    if abs(wr_diff) > divergence_threshold_wr:
        alerts.append(
            f"Win-rate divergence: {abs(wr_diff):.1%} delta "
            f"(paper={paper.win_rate:.1%}, live={live.win_rate:.1%})"
        )

    # Avg slippage delta
    slip_diff = live.avg_slippage_ticks - paper.avg_slippage_ticks
    if abs(slip_diff) > divergence_threshold_slip:
        alerts.append(
            f"Slippage divergence: {abs(slip_diff):.2f} ticks delta "
            f"(paper={paper.avg_slippage_ticks:.2f}, live={live.avg_slippage_ticks:.2f})"
        )

    is_divergent = len(alerts) > 0

    return ParityReport(
        paper=paper,
        live=live,
        trade_pnl_diff_ci=trade_pnl_diff_ci,
        expectancy_diff_dollars=float(live.expectancy_dollars - paper.expectancy_dollars),
        win_rate_diff=wr_diff,
        slippage_diff_ticks=slip_diff,
        alerts=alerts,
        is_divergent=is_divergent,
    )


def render_report(report: ParityReport) -> str:
    """Return a rich-text / markdown string suitable for CLI printing.

    Args:
        report: ParityReport to render.

    Returns:
        Formatted report string.
    """
    lines: list[str] = []

    lines.append("=" * 70)
    lines.append("PARITY REPORT: Paper vs Live")
    lines.append("=" * 70)

    # Environment summaries
    lines.append("\nPAPER TRADING:")
    lines.append(f"  Trades:        {report.paper.n_trades}")
    lines.append(f"  Total PnL:     ${float(report.paper.total_pnl):,.2f}")
    lines.append(f"  Expectancy:    ${float(report.paper.expectancy_dollars):,.2f}/trade")
    lines.append(f"  Win Rate:      {report.paper.win_rate:.1%}")
    lines.append(f"  Avg Slippage:  {report.paper.avg_slippage_ticks:.2f} ticks")
    lines.append(f"  Rejected:      {report.paper.n_rejected}")

    lines.append("\nLIVE TRADING:")
    lines.append(f"  Trades:        {report.live.n_trades}")
    lines.append(f"  Total PnL:     ${float(report.live.total_pnl):,.2f}")
    lines.append(f"  Expectancy:    ${float(report.live.expectancy_dollars):,.2f}/trade")
    lines.append(f"  Win Rate:      {report.live.win_rate:.1%}")
    lines.append(f"  Avg Slippage:  {report.live.avg_slippage_ticks:.2f} ticks")
    lines.append(f"  Rejected:      {report.live.n_rejected}")

    # Deltas
    lines.append("\nDIFFERENCES:")
    lines.append(f"  Expectancy Δ:  ${report.expectancy_diff_dollars:+.2f}/trade")
    lines.append(f"  Win Rate Δ:    {report.win_rate_diff:+.1%}")
    lines.append(f"  Slippage Δ:    {report.slippage_diff_ticks:+.2f} ticks")

    # Bootstrap CI for paired PnL diff
    lines.append("\nPAIRED PnL BOOTSTRAP (live - paper):")
    lines.append(f"  Point Est:     ${report.trade_pnl_diff_ci.point:+.2f}/trade")
    lines.append(
        f"  95% CI:        [${report.trade_pnl_diff_ci.lo:.2f}, ${report.trade_pnl_diff_ci.hi:.2f}]"
    )
    lines.append(f"  CI Width:      ${report.trade_pnl_diff_ci.width:.2f}")
    lines.append(f"  N Trades:      {report.trade_pnl_diff_ci.n}")
    lines.append(f"  Bootstrap N:   {report.trade_pnl_diff_ci.n_boot}")

    # Alerts
    lines.append(f"\nSTATUS: {'DIVERGENT ⚠️' if report.is_divergent else 'OK ✓'}")
    if report.alerts:
        lines.append(f"Alerts ({len(report.alerts)}):")
        for alert in report.alerts:
            lines.append(f"  • {alert}")
    else:
        lines.append("  (No alerts)")

    lines.append("=" * 70)

    return "\n".join(lines)
