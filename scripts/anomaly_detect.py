"""Phase D #34 — Anomaly detector (z-score outliers).

Flags trades whose PnL, duration, or size sits ≥ 3σ outside the
journal norm. Helps surface "what the hell happened here" trades
for human review.

Usage:
    python scripts/anomaly_detect.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "anomaly.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def _z(x, mean, sd):
    return (x - mean) / sd if sd else 0


def main() -> int:
    argparse.ArgumentParser().parse_args()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    trades = load_trades()
    if len(trades) < 5:
        REPORT_PATH.write_text("# Anomaly Detection\n\n_need ≥5 trades_\n")
        print("anomaly: insufficient data")
        return 0

    pnls = [t.net_pnl for t in trades]
    durs = [t.duration_s for t in trades]
    qtys = [t.qty for t in trades]

    m_pnl, sd_pnl = statistics.fmean(pnls), statistics.stdev(pnls) if len(pnls) > 1 else 1
    m_dur, sd_dur = statistics.fmean(durs), statistics.stdev(durs) if len(durs) > 1 else 1
    m_qty, sd_qty = statistics.fmean(qtys), statistics.stdev(qtys) if len(qtys) > 1 else 1

    anomalies = []
    for t in trades:
        z_pnl = _z(t.net_pnl, m_pnl, sd_pnl)
        z_dur = _z(t.duration_s, m_dur, sd_dur)
        z_qty = _z(t.qty, m_qty, sd_qty)
        if max(abs(z_pnl), abs(z_dur), abs(z_qty)) >= 3:
            anomalies.append((t, z_pnl, z_dur, z_qty))

    lines = [
        f"# Anomaly Detection · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- trades scanned: **{len(trades)}**",
        f"- anomalies (|z| ≥ 3 on any axis): **{len(anomalies)}**",
        "",
        "## Flagged trades",
        "| Seq | Time | $ PnL | z_pnl | z_dur | z_qty |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for t, zp, zd, zq in anomalies[:30]:
        when = t.exit_ts.strftime("%Y-%m-%d %H:%M:%S") if t.exit_ts else "—"
        lines.append(
            f"| {t.seq} | {when} | ${t.net_pnl:+.2f} | {zp:+.2f} | {zd:+.2f} | {zq:+.2f} |"
        )
    if not anomalies:
        lines.append("| (none) | — | — | — | — | — |")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"anomaly: {len(anomalies)} flagged · σ_pnl={sd_pnl:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
