"""Auto-generate Firm-style trade post-mortems from the journal.

Phase 3: every closed trade deserves a post-mortem, but humans won't do it
manually for every fill. This script walks the journal, selects trades
matching a filter (e.g. all losers, or the largest N losses), and stamps
them into Firm-style post-mortem reports under
``reports/post_mortems/<trade_id>.md`` using the session_log + decision_memo
templates as the prose frame.

The Firm's production Python (``desktop_app/firm/*``) is **not** imported —
we only read the markdown templates shipped in ``firm/templates/``.

Usage:

    python scripts/postmortem.py                    # default: all losers
    python scripts/postmortem.py --mode worst --n 3 # three worst trades
    python scripts/postmortem.py --mode all         # every trade
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mnq.storage.journal import EventJournal  # noqa: E402
from mnq.storage.schema import FILL_REALIZED  # noqa: E402

DEFAULT_JOURNAL = Path("/sessions/kind-keen-faraday/data/live_sim/journal.sqlite")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "reports" / "post_mortems"
TEMPLATE_DIR = REPO_ROOT / "firm" / "templates"


@dataclass
class ClosedTrade:
    order_id: str
    side: str
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    pnl_dollars: float
    commission_dollars: float
    exit_reason: str
    regime: str
    slippage_ticks: float
    entry_slip_ticks: float
    qty: int


def _load_closed_trades(path: Path) -> list[ClosedTrade]:
    """Walk the journal and pull out trade-closure FILL_REALIZED events."""
    journal = EventJournal(path)
    out: list[ClosedTrade] = []
    for entry in journal.replay(event_types=(FILL_REALIZED,)):
        p = entry.payload
        if "pnl_dollars" not in p or "entry_ts" not in p:
            continue
        try:
            pnl = float(p["pnl_dollars"])
        except (TypeError, ValueError):
            continue
        out.append(
            ClosedTrade(
                order_id=str(p.get("order_id", "")),
                side=str(p.get("side", "?")),
                entry_ts=str(p.get("entry_ts", "")),
                exit_ts=str(p.get("exit_ts", "")),
                entry_price=float(p.get("entry_price", 0) or 0),
                exit_price=float(p.get("exit_price", 0) or 0),
                pnl_dollars=pnl,
                commission_dollars=float(p.get("commission_dollars", 0) or 0),
                exit_reason=str(p.get("exit_reason", "")),
                regime=str(p.get("regime", "")),
                slippage_ticks=float(p.get("slippage_ticks", 0) or 0),
                entry_slip_ticks=float(p.get("entry_slip_ticks", 0) or 0),
                qty=int(p.get("qty", 0) or 0),
            )
        )
    return out


def _select(trades: list[ClosedTrade], mode: str, n: int) -> list[ClosedTrade]:
    """Filter trades by mode: 'losers', 'winners', 'worst', 'best', 'all'."""
    if mode == "losers":
        return [t for t in trades if t.pnl_dollars < 0]
    if mode == "winners":
        return [t for t in trades if t.pnl_dollars > 0]
    if mode == "worst":
        return sorted(trades, key=lambda t: t.pnl_dollars)[:n]
    if mode == "best":
        return sorted(trades, key=lambda t: -t.pnl_dollars)[:n]
    if mode == "all":
        return trades
    raise ValueError(f"unknown mode: {mode}")


def _derive_red_team_dissent(t: ClosedTrade) -> str:
    """Heuristics that mimic what the Firm's Red Team would have flagged."""
    notes: list[str] = []
    if t.slippage_ticks > 1.0:
        notes.append(
            f"Exit slippage {t.slippage_ticks:+.1f} ticks — realized fill was "
            "meaningfully worse than reference; spread/liquidity was adverse."
        )
    if t.entry_slip_ticks > 1.0:
        notes.append(
            f"Entry slippage {t.entry_slip_ticks:+.1f} ticks — we paid up to "
            "get in; the signal wasn't strong enough to justify chasing."
        )
    if t.exit_reason == "stop" and t.pnl_dollars < -15.0:
        notes.append(
            "Full-stop exit — the entry thesis was broken by price inside the "
            "first timebox. Either the filter gauntlet missed the regime or "
            "the signal strength was below threshold."
        )
    if t.exit_reason == "time_stop":
        notes.append(
            "Time-stop exit — thesis neither proved nor disproved. Consider "
            "whether the time_stop_bars window is too short for this regime."
        )
    if t.regime in {"trend_down", "range_bound", "chop"} and t.side == "long" and t.pnl_dollars < 0:
        notes.append(
            f"Long in {t.regime} regime — directional bias gate should have "
            "suppressed this signal. Check `allow_long` / trend_align_bars."
        )
    if t.regime in {"trend_up", "range_bound", "chop"} and t.side == "short" and t.pnl_dollars < 0:
        notes.append(
            f"Short in {t.regime} regime — directional bias gate should have "
            "suppressed this signal. Check `allow_short` / trend_align_bars."
        )
    if not notes:
        notes.append(
            "No automatic dissent triggered — trade failed cleanly within the "
            "stated risk envelope. This is the class of loss the system is "
            "designed to absorb."
        )
    return "\n".join(f"- {n}" for n in notes)


def _falsification_criteria(t: ClosedTrade, all_trades: list[ClosedTrade]) -> str:
    """Produce falsification criteria tied to this regime/setup."""
    regime = t.regime or "unknown"
    side = t.side or "?"
    regime_trades = [x for x in all_trades if x.regime == regime and x.side == side]
    n = len(regime_trades)
    wins = sum(1 for x in regime_trades if x.pnl_dollars > 0)
    wr = wins / n if n else 0.0
    return (
        f"- In the `{regime}` / `{side}` bucket after the next 5 closed trades, "
        f"win-rate must be ≥ {max(0.40, wr):.0%} (current: {wr:.1%} on n={n}).\n"
        "- Mean slippage for this bucket must be ≤ +1.5 ticks.\n"
        f"- Net PnL in this bucket across the next {max(5, n)} trades must be "
        "positive; if not, this setup is retired."
    )


def _render_postmortem(t: ClosedTrade, all_trades: list[ClosedTrade]) -> str:
    outcome = "WIN" if t.pnl_dollars > 0 else ("LOSS" if t.pnl_dollars < 0 else "SCRATCH")
    thesis = (
        f"{t.side.upper()} entry on {t.entry_ts} against a `{t.regime}` tape. "
        f"Exit via `{t.exit_reason}` at {t.exit_price:.2f} vs entry {t.entry_price:.2f}."
    )
    evidence = (
        f"- PnL: ${t.pnl_dollars:+,.2f} (commission ${t.commission_dollars:+.2f})\n"
        f"- Entry slippage: {t.entry_slip_ticks:+.1f} ticks; exit slippage: "
        f"{t.slippage_ticks:+.1f} ticks.\n"
        f"- Regime at entry: `{t.regime}`; qty: {t.qty}."
    )
    dissent = _derive_red_team_dissent(t)
    falsification = _falsification_criteria(t, all_trades)
    resolution = (
        "[x] Accepted as surviving risk — monitoring: next 5 trades in this bucket."
        if outcome != "LOSS"
        else "[ ] Fixed — how: adjust gauntlet; [x] Accepted as surviving risk — monitoring per falsification criteria; [ ] Overridden."
    )
    return f"""# Post-Mortem — {t.order_id} ({outcome})

Auto-generated from journal on close. Consumes only `firm/templates/*`
markdown; the in-progress `desktop_app/firm/*` Python is untouched.

## Thesis

{thesis}

## Evidence

{evidence}

## Red Team's primary dissent

{dissent}

## Resolution

{resolution}

## Falsification

I abandon this setup if ANY of:

{falsification}

## Monitoring

- First review: after the next 5 trades in the `{t.regime}` / `{t.side}` bucket.
- Success: bucket expectancy > $0 and exit slippage p95 ≤ +2.0 ticks.
- Failure: bucket net PnL < 0 across those 5 trades.

## Raw payload snapshot

```
order_id           = {t.order_id}
side               = {t.side}
entry_ts / exit_ts = {t.entry_ts} → {t.exit_ts}
entry / exit price = {t.entry_price:.2f} → {t.exit_price:.2f}
pnl_dollars        = {t.pnl_dollars:+.4f}
commission         = {t.commission_dollars:+.4f}
exit_reason        = {t.exit_reason}
regime             = {t.regime}
slippage_ticks     = {t.slippage_ticks:+.2f}
entry_slip_ticks   = {t.entry_slip_ticks:+.2f}
```
"""


def _render_index(written: list[Path], mode: str) -> str:
    lines: list[str] = [f"# Post-Mortem Index — mode `{mode}`", ""]
    lines.append(f"- post-mortems written: **{len(written)}**")
    lines.append("")
    for p in written:
        lines.append(f"- [{p.stem}]({p.name})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto post-mortem generator.")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument(
        "--mode",
        type=str,
        default="losers",
        choices=["all", "losers", "winners", "worst", "best"],
    )
    parser.add_argument("--n", type=int, default=3, help="For 'worst'/'best' modes.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    if not args.journal.exists():
        print(f"journal not found: {args.journal}", file=sys.stderr)
        return 2

    trades = _load_closed_trades(args.journal)
    chosen = _select(trades, args.mode, args.n)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for t in chosen:
        if not t.order_id:
            continue
        md = _render_postmortem(t, trades)
        dest = args.output_dir / f"{t.order_id[:12]}_{t.exit_reason}.md"
        dest.write_text(md)
        written.append(dest)

    index_path = args.output_dir / "INDEX.md"
    index_path.write_text(_render_index(written, args.mode))

    print(f"wrote {len(written)} post-mortems to {args.output_dir}")
    print(f"index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
