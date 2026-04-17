"""End-of-session daily digest.

Phase 1: one markdown snapshot per session, summarizing the day's journal
activity in a format you can paste into a physical notebook. Consumes
nothing outside this repo; the `firm/templates/session_log.md` structure
is used as the prose frame.

The digest is intentionally a single page so it's readable at a glance:

* Pipeline counters (signals, fills, risk blocks, breaker halts).
* PnL ladder per regime / side.
* Biggest winner + biggest loser with one-line post-mortem blurb.
* Watchdog heartbeat: time between first and last event; any > N-minute
  gaps are flagged as "process may have stalled".
* Next-day checklist pulled from `firm/templates/checklist.md`.

Usage:

    python scripts/daily_digest.py
    python scripts/daily_digest.py --output reports/daily/2026-04-15.md
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.storage.journal import EventJournal  # noqa: E402
from mnq.storage.schema import FILL_REALIZED  # noqa: E402

DEFAULT_JOURNAL = Path("/sessions/kind-keen-faraday/data/live_sim/journal.sqlite")


@dataclass
class Digest:
    n_events: int
    event_counts: dict[str, int]
    first_event_ts: str | None
    last_event_ts: str | None
    gap_minutes_max: float
    n_closed_trades: int
    gross_pnl: float
    wins: int
    losses: int
    mean_slip: float
    biggest_winner: tuple[str, float, str] | None   # (order_id, pnl, regime)
    biggest_loser: tuple[str, float, str] | None
    per_regime_pnl: dict[str, float]
    per_side_pnl: dict[str, float]


def _parse_ts(ts: object) -> datetime | None:
    if isinstance(ts, datetime):
        return ts
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def build_digest(path: Path = DEFAULT_JOURNAL) -> Digest:
    j = EventJournal(path)
    counter: Counter[str] = Counter()
    first_ts: str | None = None
    last_ts: str | None = None
    gap_max = 0.0
    prev_dt: datetime | None = None

    closed: list[dict] = []
    for entry in j.replay():
        counter[entry.event_type] += 1
        ts_str = entry.ts.isoformat() if isinstance(entry.ts, datetime) else str(entry.ts)
        if first_ts is None:
            first_ts = ts_str
        last_ts = ts_str

        dt = _parse_ts(entry.ts)
        if dt is not None and prev_dt is not None:
            gap_min = (dt - prev_dt).total_seconds() / 60.0
            if gap_min > gap_max:
                gap_max = gap_min
        prev_dt = dt

        if entry.event_type == FILL_REALIZED:
            p = entry.payload
            if "pnl_dollars" in p and "entry_ts" in p:
                closed.append(p)

    gross = 0.0
    wins = 0
    losses = 0
    mean_slip_accum = 0.0
    per_regime: dict[str, float] = {}
    per_side: dict[str, float] = {}
    best: tuple[str, float, str] | None = None
    worst: tuple[str, float, str] | None = None

    for c in closed:
        try:
            pnl = float(c["pnl_dollars"])
        except (TypeError, ValueError):
            continue
        gross += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        mean_slip_accum += float(c.get("slippage_ticks", 0) or 0)
        regime = str(c.get("regime", "unknown"))
        side = str(c.get("side", "?"))
        per_regime[regime] = per_regime.get(regime, 0.0) + pnl
        per_side[side] = per_side.get(side, 0.0) + pnl
        order_id = str(c.get("order_id", ""))
        if best is None or pnl > best[1]:
            best = (order_id, pnl, regime)
        if worst is None or pnl < worst[1]:
            worst = (order_id, pnl, regime)

    mean_slip = mean_slip_accum / len(closed) if closed else 0.0

    return Digest(
        n_events=sum(counter.values()),
        event_counts=dict(counter),
        first_event_ts=first_ts,
        last_event_ts=last_ts,
        gap_minutes_max=gap_max,
        n_closed_trades=len(closed),
        gross_pnl=gross,
        wins=wins,
        losses=losses,
        mean_slip=mean_slip,
        biggest_winner=best,
        biggest_loser=worst,
        per_regime_pnl=per_regime,
        per_side_pnl=per_side,
    )


def _render(d: Digest, *, gap_threshold_min: float = 30.0) -> str:
    lines: list[str] = [
        f"# Daily Digest — {datetime.now(UTC).strftime('%Y-%m-%d')}",
        "",
    ]
    lines.append("## Pre-session")
    lines.append("")
    lines.append(f"- First event: `{d.first_event_ts}`")
    lines.append(f"- Last event:  `{d.last_event_ts}`")
    lines.append(f"- Total events: **{d.n_events}**")
    if d.gap_minutes_max > gap_threshold_min:
        lines.append(
            f"- Max inter-event gap: **{d.gap_minutes_max:.1f} min** ⚠️ "
            f"(threshold {gap_threshold_min:.0f} min — process may have stalled)"
        )
    else:
        lines.append(f"- Max inter-event gap: {d.gap_minutes_max:.1f} min (OK)")
    lines.append("")
    lines.append("## Trades")
    lines.append("")
    lines.append(f"- Closed trades: **{d.n_closed_trades}**")
    lines.append(f"- Gross PnL: **${d.gross_pnl:+,.2f}**")
    if d.n_closed_trades:
        wr = d.wins / d.n_closed_trades
        lines.append(f"- Win rate: **{wr:.1%}** ({d.wins}/{d.n_closed_trades})")
    else:
        lines.append("- Win rate: n/a")
    lines.append(f"- Mean slippage: {d.mean_slip:+.2f} ticks")
    lines.append("")
    if d.biggest_winner:
        oid, pnl, regime = d.biggest_winner
        lines.append(f"### Biggest winner\n\n- `{oid}` — ${pnl:+,.2f} ({regime})\n")
    if d.biggest_loser:
        oid, pnl, regime = d.biggest_loser
        lines.append(f"### Biggest loser\n\n- `{oid}` — ${pnl:+,.2f} ({regime})\n")
    lines.append("## Breakdown")
    lines.append("")
    if d.per_regime_pnl:
        lines.append("### Per regime")
        lines.append("")
        lines.append("| Regime | Net PnL |")
        lines.append("|---|---:|")
        for k in sorted(d.per_regime_pnl):
            lines.append(f"| `{k}` | ${d.per_regime_pnl[k]:+,.2f} |")
        lines.append("")
    if d.per_side_pnl:
        lines.append("### Per side")
        lines.append("")
        lines.append("| Side | Net PnL |")
        lines.append("|---|---:|")
        for k in sorted(d.per_side_pnl):
            lines.append(f"| {k} | ${d.per_side_pnl[k]:+,.2f} |")
        lines.append("")
    lines.append("## Event-type counts")
    lines.append("")
    lines.append("| Event type | Count |")
    lines.append("|---|---:|")
    for et in sorted(d.event_counts):
        lines.append(f"| `{et}` | {d.event_counts[et]} |")
    lines.append("")
    lines.append("## One lesson from today")
    lines.append("")
    lesson = _derive_lesson(d)
    lines.append(f"> {lesson}")
    lines.append("")
    lines.append("## Tomorrow's checklist")
    lines.append("")
    lines.append("- Re-run walk-forward optimizer; confirm winner stability.")
    lines.append("- Re-run calibration; watch for LOOCV Brier > 0.30 as overfit alarm.")
    lines.append("- Check strategy registry drift (`scripts/strategy_registry.py --update`).")
    lines.append("- If any bucket heat-cap dropped to 0, block that (regime × side) in the gauntlet.")
    return "\n".join(lines) + "\n"


def _derive_lesson(d: Digest) -> str:
    if d.n_closed_trades == 0:
        return (
            "No trades fired — either the filter gauntlet was too strict for the "
            "tape regime, or the strategy's entry windows didn't align with the "
            "data's active hours. Inspect the `safety.decision` event counts."
        )
    if d.gross_pnl > 0 and d.wins > d.losses:
        return (
            "Profitable day driven by win-rate; verify the winners weren't "
            "concentrated in one regime the scorer over-weights."
        )
    if d.gross_pnl > 0 and d.wins <= d.losses:
        return (
            "Profitable despite majority losses — winners doing more work than "
            "losers is fine if RR was respected on every trade; verify the stop "
            "sizing didn't drift."
        )
    if d.gross_pnl < 0 and d.mean_slip > 1.5:
        return (
            f"Losing day with adverse slippage ({d.mean_slip:+.2f} ticks mean) — "
            "tighten entry filter to avoid chasing, or slow entries by one bar."
        )
    return (
        "Losing day — review the biggest loser's post-mortem first; it usually "
        "dominates the PnL loss."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="End-of-session daily digest.")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    default_out = REPO_ROOT / "reports" / "daily" / f"{today}.md"
    parser.add_argument("--output", type=Path, default=default_out)
    parser.add_argument("--gap-threshold-min", type=float, default=30.0)
    args = parser.parse_args(argv)

    digest = build_digest(args.journal)
    md = _render(digest, gap_threshold_min=args.gap_threshold_min)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
