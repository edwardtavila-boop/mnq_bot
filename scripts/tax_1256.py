"""Phase D #38 — IRS §1256 tax tracker.

Section 1256 contracts (futures, broad-based index options) get
60/40 long-term/short-term treatment regardless of holding period.
This script aggregates the YTD net gain/loss and estimates tax.

Assumptions (edit as needed): 35% federal bracket, state 0%.

Usage:
    python scripts/tax_1256.py
    python scripts/tax_1256.py --federal 0.32 --state 0.093
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "tax_1256.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--federal", type=float, default=0.35)
    p.add_argument("--state", type=float, default=0.0)
    args = p.parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# §1256 Tax Tracker\n\n_no trades_\n")
        print("tax_1256: no trades")
        return 0

    this_year = datetime.now(UTC).year
    ytd = [t for t in trades if t.exit_ts and t.exit_ts.year == this_year]
    ytd_net = sum(t.net_pnl for t in ytd)

    # 60/40 rule
    lt_portion = ytd_net * 0.60
    st_portion = ytd_net * 0.40
    # Blended rate: long-term fed cap gains (~20% at high bracket), short-term ordinary
    lt_rate = 0.20
    st_rate = args.federal
    tax_fed = max(0.0, lt_portion * lt_rate + st_portion * st_rate)
    tax_state = max(0.0, ytd_net * args.state)
    tax_total = tax_fed + tax_state
    net_after_tax = ytd_net - tax_total

    lines = [
        f"# §1256 Tax Tracker · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- year: **{this_year}**",
        f"- YTD trades: **{len(ytd)}**",
        f"- YTD gross: **${ytd_net:+,.2f}**",
        "",
        "## §1256 60/40 split",
        f"- 60% long-term (fed rate {lt_rate:.0%}): **${lt_portion * lt_rate:,.2f}**",
        f"- 40% short-term (fed rate {st_rate:.0%}): **${st_portion * st_rate:,.2f}**",
        f"- state portion (rate {args.state:.0%}): **${tax_state:,.2f}**",
        "",
        f"- estimated total tax: **${tax_total:,.2f}**",
        f"- net after tax: **${net_after_tax:+,.2f}**",
        "",
        "_Rough estimate only. Consult a CPA for actual filings (Form 6781)._",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"tax_1256: gross=${ytd_net:.2f} est_tax=${tax_total:.2f} net=${net_after_tax:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
