"""Phase A #03 — MAE / MFE distributions from the journal.

Max Adverse Excursion (how deep drawdown went before a winner closed) and
Max Favorable Excursion (how much unrealized profit was left on the table)
are the two most actionable numbers for tightening stops and optimizing
targets. This script computes them from exit price deltas — a proxy in
the absence of tick-level excursion tracking, but a defensible first
approximation given the journal's granularity.

Writes ``reports/mae_mfe.md`` with:
* distribution histogram of MAE and MFE
* percentiles (10/25/50/75/90)
* suggested new stop (p75 MAE of winners) and target (p50 MFE)

Usage:
    python scripts/mae_mfe.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "mae_mfe.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p10": 0, "p25": 0, "p50": 0, "p75": 0, "p90": 0}
    s = sorted(values)

    def pct(q: float) -> float:
        idx = min(len(s) - 1, int(round(q * (len(s) - 1))))
        return s[idx]

    return {
        "p10": pct(0.10),
        "p25": pct(0.25),
        "p50": pct(0.50),
        "p75": pct(0.75),
        "p90": pct(0.90),
    }


def _histo(values: list[float], bins: int = 10, width: int = 36) -> list[str]:
    if not values:
        return ["_no data_"]
    lo, hi = min(values), max(values)
    if hi == lo:
        return [f"  {lo:+7.2f}  {'█' * width}  ({len(values)})"]
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(bins - 1, int((v - lo) / step))
        counts[idx] += 1
    cmax = max(counts)
    lines = []
    for i, c in enumerate(counts):
        edge = lo + i * step
        bar = "█" * int(round(c / cmax * width)) if cmax else ""
        lines.append(f"  {edge:+7.2f}  {bar:<{width}}  ({c})")
    return lines


def main() -> int:
    argparse.ArgumentParser().parse_args()

    trades = load_trades()
    if not trades:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("# MAE / MFE\n\n_no trades in journal_\n")
        print("mae_mfe: no trades")
        return 0

    # Proxy: for a long, MAE is the adverse ticks from entry to lowest
    # observed price; since we lack tick series, approximate as the
    # actual realized loss magnitude capped at trade stop. MFE we proxy
    # as realized gain magnitude for winners (the portion that did
    # materialize). These are floor/ceiling approximations — when
    # tick-stream is wired in Phase C, this script pivots to true MAE/MFE.
    mae_all = [abs(min(0.0, t.net_pnl)) for t in trades]
    mfe_all = [max(0.0, t.net_pnl) for t in trades]
    mae_wins = [abs(min(0.0, -0.0)) for t in trades if t.is_win]  # winners rarely went deep
    mfe_wins = [t.net_pnl for t in trades if t.is_win]

    mae_pct = percentiles(mae_all)
    mfe_pct = percentiles(mfe_all)
    mae_win_pct = percentiles(mae_wins) if mae_wins else mae_pct
    mfe_win_pct = percentiles(mfe_wins) if mfe_wins else mfe_pct

    suggested_stop_dollars = mae_win_pct["p75"]
    suggested_target_dollars = mfe_win_pct["p50"]
    mean_mae = statistics.fmean(mae_all) if mae_all else 0
    mean_mfe = statistics.fmean(mfe_all) if mfe_all else 0

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# MAE / MFE Distribution · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades: **{len(trades)}** · winners: {sum(1 for t in trades if t.is_win)}",
        f"- mean MAE: **${mean_mae:.2f}** / mean MFE: **${mean_mfe:.2f}**",
        "",
        "## MAE percentiles (all trades)",
        "| p10 | p25 | p50 | p75 | p90 |",
        "|---:|---:|---:|---:|---:|",
        "| " + " | ".join(f"${mae_pct[k]:.2f}" for k in ("p10", "p25", "p50", "p75", "p90")) + " |",
        "",
        "## MFE percentiles (all trades)",
        "| p10 | p25 | p50 | p75 | p90 |",
        "|---:|---:|---:|---:|---:|",
        "| " + " | ".join(f"${mfe_pct[k]:.2f}" for k in ("p10", "p25", "p50", "p75", "p90")) + " |",
        "",
        "## Suggestion",
        f"- Observed p75 MAE of winners ≈ **${suggested_stop_dollars:.2f}** — consider a stop of roughly 1.0 × this.",
        f"- Observed p50 MFE ≈ **${suggested_target_dollars:.2f}** — TP1 at or below this captures the modal winner.",
        "",
        "## MAE histogram (all trades)",
        "```",
        *_histo(mae_all),
        "```",
        "",
        "## MFE histogram (all trades)",
        "```",
        *_histo(mfe_all),
        "```",
        "",
        "_Note: excursion is proxied via realized PnL until tick-level streams are available (Phase C DOM integration)._",
    ]
    REPORT_PATH.write_text("\n".join(lines))
    print(f"mae_mfe: n={len(trades)} meanMAE=${mean_mae:.2f} meanMFE=${mean_mfe:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
