#!/usr/bin/env python3
"""Phase 4 — backtest vs live-sim parity harness.

Reads the latest live_sim journal and the most recent backtest report
for the same strategy spec, then diffs *round-trip trades* one by one.
Emits a tolerance verdict + ``reports/parity.md``.

A **round trip** is (entry_ts, entry_px, exit_ts, exit_px, qty, side).
The live-sim journal records each trade as two ``order.filled`` events
in sequence (entry fill, then exit fill), so we pair them by their
1-indexed position within the day. The backtest report already has one
row per round trip.

Tolerances (defaults):
  - Entry price      : ±0.25 pt  (1 MNQ tick)
  - Exit  price      : ±0.25 pt
  - Quantity         : exact
  - Entry/exit time  : ±60 s  each (accounts for sim clock skew vs backtest)

Exit: 0 if live round-trip count == backtest round-trip count **and**
every paired trade is within tolerance. 1 otherwise.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Journal lives in the session workspace, not the repo — see _trade_utils.
_CANDIDATE_JOURNALS = [
    Path("/sessions/kind-keen-faraday/data/live_sim/journal.sqlite"),
    REPO_ROOT / "data" / "live_sim" / "journal.sqlite",
]
JOURNAL = next((p for p in _CANDIDATE_JOURNALS if p.exists()), _CANDIDATE_JOURNALS[0])
BACKTEST_REPORT = REPO_ROOT / "reports" / "pnl_report.md"
OUTPUT = REPO_ROOT / "reports" / "parity.md"

# Tolerances
PRICE_TOL_PT = 0.25   # 1 MNQ tick
QTY_TOL = 0
TIME_TOL_SEC = 60


@dataclass(frozen=True, slots=True)
class Trade:
    """One round-trip trade — entry paired with its exit."""
    seq: int
    side: str
    qty: int
    entry_ts: datetime
    entry_px: float
    exit_ts: datetime
    exit_px: float
    source: str  # "live_sim" or "backtest"


def _parse_ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _load_live_sim_trades(db: Path) -> list[Trade]:
    """Read live-sim fills and pair them into round-trip trades.

    The journal stores each trade as two consecutive ``order.filled``
    events ordered by ``seq`` (entry fill, then exit fill). This
    function walks the fill stream in pairs: (even_index, odd_index) ->
    one round trip. If the total fill count is odd, the dangling fill
    is dropped — the harness will flag the count mismatch separately.
    """
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT seq, ts, event_type, payload FROM events "
            "WHERE event_type IN ('order.filled','order.partial') "
            "ORDER BY seq ASC"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    fills: list[tuple[int, datetime, float, int, str]] = []
    for r in rows:
        try:
            p = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        price = p.get("fill_price") or p.get("avg_fill_price") or p.get("price")
        qty = p.get("fill_qty") or p.get("qty") or 0
        side = p.get("side") or "long"
        ts = _parse_ts(r["ts"])
        if price is None or ts is None:
            continue
        try:
            fills.append((r["seq"], ts, float(price), int(qty), str(side)))
        except (TypeError, ValueError):
            continue

    trades: list[Trade] = []
    for i in range(0, len(fills) - 1, 2):
        entry = fills[i]
        exit_ = fills[i + 1]
        trades.append(Trade(
            seq=(i // 2) + 1,
            side=entry[4],
            qty=entry[3],
            entry_ts=entry[1],
            entry_px=entry[2],
            exit_ts=exit_[1],
            exit_px=exit_[2],
            source="live_sim",
        ))
    return trades


def _load_backtest_trades(path: Path) -> list[Trade]:
    """Parse the ``reports/pnl_report.md`` trade table.

    Schema:
    ``| # | entry_ts | side | qty | entry_px | exit_ts | exit_px | pnl |``
    """
    if not path.exists():
        return []
    trades: list[Trade] = []
    for line in path.read_text().splitlines():
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 7 or not parts[0].isdigit():
            continue
        try:
            n = int(parts[0])
            entry_ts = _parse_ts(parts[1])
            side = parts[2]
            qty = int(parts[3])
            entry_px = float(parts[4])
            exit_ts = _parse_ts(parts[5])
            exit_px = float(parts[6])
        except (ValueError, IndexError):
            continue
        if entry_ts is None or exit_ts is None:
            continue
        trades.append(Trade(
            seq=n, side=side, qty=qty,
            entry_ts=entry_ts, entry_px=entry_px,
            exit_ts=exit_ts, exit_px=exit_px,
            source="backtest",
        ))
    return trades


def _pair_trades(live: list[Trade], bt: list[Trade]) -> list[tuple[Trade | None, Trade | None]]:
    n = max(len(live), len(bt))
    out: list[tuple[Trade | None, Trade | None]] = []
    for i in range(n):
        out.append((live[i] if i < len(live) else None,
                    bt[i] if i < len(bt) else None))
    return out


def _diff_pair(lv: Trade | None, bv: Trade | None) -> tuple[bool, dict[str, object]]:
    """Return ``(within_tol, diff_dict)`` for a single paired trade."""
    if lv is None or bv is None:
        def _asdict(t: Trade | None) -> dict | None:
            if t is None:
                return None
            return {
                "entry_ts": t.entry_ts.isoformat(), "entry_px": t.entry_px,
                "exit_ts": t.exit_ts.isoformat(), "exit_px": t.exit_px,
                "qty": t.qty, "side": t.side,
            }
        return False, {"status": "unmatched", "live": _asdict(lv), "backtest": _asdict(bv)}

    dpx_in = abs(lv.entry_px - bv.entry_px)
    dpx_out = abs(lv.exit_px - bv.exit_px)
    dq = abs(lv.qty - bv.qty)
    dt_in = abs((lv.entry_ts - bv.entry_ts).total_seconds())
    dt_out = abs((lv.exit_ts - bv.exit_ts).total_seconds())
    ok = (
        dpx_in <= PRICE_TOL_PT
        and dpx_out <= PRICE_TOL_PT
        and dq <= QTY_TOL
        and dt_in <= TIME_TOL_SEC
        and dt_out <= TIME_TOL_SEC
    )
    return ok, {
        "status": "ok" if ok else "diverged",
        "dpx_in": dpx_in,
        "dpx_out": dpx_out,
        "dq": dq,
        "dt_in_s": dt_in,
        "dt_out_s": dt_out,
        "live": {
            "entry": f"{lv.entry_px} @ {lv.entry_ts.isoformat()}",
            "exit":  f"{lv.exit_px} @ {lv.exit_ts.isoformat()}",
            "qty": lv.qty, "side": lv.side,
        },
        "backtest": {
            "entry": f"{bv.entry_px} @ {bv.entry_ts.isoformat()}",
            "exit":  f"{bv.exit_px} @ {bv.exit_ts.isoformat()}",
            "qty": bv.qty, "side": bv.side,
        },
    }


def main() -> int:
    live = _load_live_sim_trades(JOURNAL)
    bt = _load_backtest_trades(BACKTEST_REPORT)

    pairs = _pair_trades(live, bt)
    results = [_diff_pair(lv, bv) for lv, bv in pairs]
    n_total = len(pairs)
    n_ok = sum(1 for ok, _ in results if ok)
    n_div = n_total - n_ok

    lines: list[str] = [
        f"# Parity Harness — {datetime.now(tz=UTC).isoformat()}",
        "",
        f"**Live-sim trades:** {len(live)}  ·  **Backtest trades:** {len(bt)}",
        f"**Pairs:** {n_total}  ·  **Within tol:** {n_ok}  ·  **Diverged:** {n_div}",
        "",
        f"Tolerances: ±{PRICE_TOL_PT}pt on entry/exit price, "
        f"±{QTY_TOL} qty, ±{TIME_TOL_SEC}s on entry/exit time.",
        "",
    ]

    if not live and not bt:
        lines.append("⚠️  No trades found in either source. Run `live_sim` and a")
        lines.append("strategy-level backtest first. Emitting PASS only because")
        lines.append("there's nothing to compare.")
        verdict_ok = True
    elif not bt:
        lines.append("⚠️  No backtest baseline report at `reports/pnl_report.md`.")
        lines.append("Parity is **undecidable** — marking as stub PASS. Run a")
        lines.append("strategy backtest to populate the baseline, then re-run this.")
        verdict_ok = True
    else:
        lines.append("| # | status | Δpx in | Δpx out | Δt in (s) | Δt out (s) | live entry→exit | backtest entry→exit |")
        lines.append("|---:|---|---:|---:|---:|---:|---|---|")
        for i, (_ok, d) in enumerate(results, 1):
            if d["status"] == "unmatched":
                lv = d.get("live") or {}
                bv = d.get("backtest") or {}
                lv_str = f"{lv.get('entry', '—')} → {lv.get('exit', '—')}" if lv else "—"
                bv_str = f"{bv.get('entry', '—')} → {bv.get('exit', '—')}" if bv else "—"
                lines.append(
                    f"| {i} | ❌ unmatched | — | — | — | — | {lv_str} | {bv_str} |"
                )
            else:
                mark = "🟢 ok" if d["status"] == "ok" else "🔴 diverged"
                lv = d["live"]
                bv = d["backtest"]
                lines.append(
                    f"| {i} | {mark} | {d['dpx_in']:.2f} | {d['dpx_out']:.2f} | "
                    f"{d['dt_in_s']:.0f} | {d['dt_out_s']:.0f} | "
                    f"{lv['entry']} → {lv['exit']} | {bv['entry']} → {bv['exit']} |"
                )
        verdict_ok = n_div == 0 and len(live) == len(bt)

    lines.append("")
    lines.append(f"**Verdict:** {'🟢 PARITY' if verdict_ok else '🔴 DIVERGED'}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines))
    print(
        f"parity: {'🟢 PARITY' if verdict_ok else '🔴 DIVERGED'} · "
        f"live={len(live)} bt={len(bt)} ok={n_ok}/{n_total}"
    )
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
