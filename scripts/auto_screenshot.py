"""Phase B #15 — Auto-screenshot harness (stub + contract).

When a trade closes, capture the chart window at entry±15 bars and
drop it into ``reports/screenshots/<seq>.png``. Until an actual
screenshot provider is wired in (TradingView, NinjaTrader, Tradovate
chart), this stubs the interface and writes a placeholder svg so the
review pipeline is unblocked.

Usage:
    python scripts/auto_screenshot.py --seq 42
    python scripts/auto_screenshot.py --all
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SHOTS_DIR = REPO_ROOT / "reports" / "screenshots"
REPORT_PATH = REPO_ROOT / "reports" / "auto_screenshot.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402

PLACEHOLDER_SVG_TEMPLATE = """\
<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
  <rect width="960" height="540" fill="#0a0e14"/>
  <text x="40" y="80" font-family="monospace" font-size="22" fill="#50fa7b">trade #{seq}</text>
  <text x="40" y="120" font-family="monospace" font-size="14" fill="#888">{side} · {qty}c · ${pnl:+.2f} · {r:+.2f}R</text>
  <text x="40" y="150" font-family="monospace" font-size="12" fill="#666">entry: {entry_ts} @ ${entry_px:.2f}</text>
  <text x="40" y="170" font-family="monospace" font-size="12" fill="#666">exit : {exit_ts} @ ${exit_px:.2f}</text>
  <text x="40" y="500" font-family="monospace" font-size="11" fill="#444">
    [placeholder — wire TradingView Capture API for real bars]
  </text>
</svg>
"""


def _shoot(trade) -> Path:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = SHOTS_DIR / f"{trade.seq:06d}.svg"
    out.write_text(PLACEHOLDER_SVG_TEMPLATE.format(
        seq=trade.seq, side=trade.side, qty=trade.qty,
        pnl=trade.net_pnl, r=trade.r_multiple,
        entry_ts=trade.entry_ts.isoformat() if trade.entry_ts else "—",
        exit_ts=trade.exit_ts.isoformat() if trade.exit_ts else "—",
        entry_px=trade.entry_price or 0, exit_px=trade.exit_price or 0,
    ))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seq", type=int, help="Single trade seq")
    p.add_argument("--all", action="store_true", help="Snap all trades")
    p.add_argument("--last", type=int, default=10, help="Snap last N trades")
    args = p.parse_args()

    trades = load_trades()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not trades:
        REPORT_PATH.write_text("# Screenshot Harness\n\n_no trades in journal_\n")
        print("auto_screenshot: no trades")
        return 0

    if args.seq:
        targets = [t for t in trades if t.seq == args.seq]
    elif args.all:
        targets = trades
    else:
        targets = trades[-args.last:]

    shot_paths = []
    for t in targets:
        shot_paths.append(_shoot(t))

    lines = [
        f"# Auto-Screenshot · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- captured: **{len(shot_paths)}** placeholder SVGs",
        f"- location: `{SHOTS_DIR.relative_to(REPO_ROOT)}`",
        "",
        "## Files",
    ]
    for p_ in shot_paths[-20:]:
        lines.append(f"- `{p_.name}`")
    lines += ["", "_Next step: bind to TradingView Capture API or NinjaTrader chart export._"]
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"auto_screenshot: captured {len(shot_paths)} placeholder charts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
