"""Shared helpers for Phase A-D scripts.

Extracts completed trades from the live_sim journal.

The journal uses per-lifecycle trace_ids — an order lifecycle trace
contains the two order.filled events (entry + exit), a separate trace
contains the pnl.update for that trade, and a third trace contains the
position.update. We reconstruct trades by pairing them in sequence order.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_JOURNAL = Path("/sessions/kind-keen-faraday/data/live_sim/journal.sqlite")


@dataclass
class Trade:
    """Single completed trade extracted from the journal."""

    seq: int = 0
    entry_ts: datetime | None = None
    exit_ts: datetime | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    qty: int = 1
    side: str = "unknown"
    net_pnl: float = 0.0
    slippage_ticks: float = 0.0
    latency_ms: float = 0.0
    mae_ticks: float = 0.0
    mfe_ticks: float = 0.0
    setup: str = "unknown"
    regime: str = "unknown"
    followed_rules: bool | None = None
    extras: dict = field(default_factory=dict)

    @property
    def hour(self) -> int | None:
        return (self.exit_ts.hour if self.exit_ts else None)

    @property
    def weekday(self) -> int | None:
        return (self.exit_ts.weekday() if self.exit_ts else None)

    @property
    def duration_s(self) -> float:
        if self.entry_ts and self.exit_ts:
            return (self.exit_ts - self.entry_ts).total_seconds()
        return 0.0

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0

    @property
    def r_multiple(self) -> float:
        """Dollar PnL → R-multiples (risk = 2 ticks × $0.50/tick per contract)."""
        risk_per_contract = 2 * 0.25 * 2.0  # MNQ: $2/pt, 2-tick baseline = $1
        denom = max(1, abs(self.qty)) * risk_per_contract
        return self.net_pnl / denom


def load_trades(journal_path: Path = DEFAULT_JOURNAL) -> list[Trade]:
    """Reconstruct completed trades from the event journal.

    Strategy: walk events in seq order; each ``pnl.update`` marks the
    close of a trade. The two most-recent ``order.filled`` rows before
    the pnl.update are the entry and exit fills. Position.update events
    (if present) refine qty/side.
    """
    if not journal_path.exists():
        return []

    conn = sqlite3.connect(journal_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT seq, ts, event_type, trace_id, payload FROM events ORDER BY seq"
    ).fetchall()
    conn.close()

    trades: list[Trade] = []
    pending_fills: list[tuple[datetime, float, int]] = []
    pending_slippage: list[float] = []
    pending_latency: list[float] = []
    last_position: dict = {}

    for r in rows:
        ts = datetime.fromisoformat(r["ts"])
        et = r["event_type"]
        payload = json.loads(r["payload"]) if r["payload"] else {}

        if et == "order.filled":
            price = float(payload.get("avg_fill_price", 0) or payload.get("price", 0) or 0)
            qty = int(payload.get("filled_qty", 0) or payload.get("qty", 0) or 1)
            pending_fills.append((ts, price, qty))

        elif et == "fill.realized":
            pending_slippage.append(float(payload.get("slippage_ticks", 0) or 0))
            pending_latency.append(float(payload.get("latency_ms", 0) or 0))

        elif et == "position.update":
            last_position = payload

        elif et == "pnl.update":
            if len(pending_fills) < 2:
                pending_fills.clear()
                pending_slippage.clear()
                pending_latency.clear()
                continue

            entry = pending_fills[0]
            exit_ = pending_fills[-1]
            qty = max(entry[2], exit_[2], int(last_position.get("qty", 1) or 1))
            net_pnl = float(payload.get("net_pnl", 0) or 0)
            side = "unknown"
            if entry[1] and exit_[1]:
                if net_pnl > 0 and exit_[1] > entry[1]:
                    side = "long"
                elif net_pnl > 0 and exit_[1] < entry[1]:
                    side = "short"
                elif net_pnl < 0 and exit_[1] > entry[1]:
                    side = "short"
                elif net_pnl < 0 and exit_[1] < entry[1]:
                    side = "long"
                elif exit_[1] >= entry[1]:
                    side = "long"
                else:
                    side = "short"

            t = Trade(
                seq=r["seq"],
                entry_ts=entry[0],
                exit_ts=exit_[0],
                entry_price=entry[1],
                exit_price=exit_[1],
                qty=qty,
                side=side,
                net_pnl=net_pnl,
                slippage_ticks=sum(pending_slippage),
                latency_ms=max(pending_latency) if pending_latency else 0.0,
                regime=str(last_position.get("regime", "unknown")),
                setup=str(last_position.get("setup", "unknown")),
            )
            trades.append(t)
            pending_fills.clear()
            pending_slippage.clear()
            pending_latency.clear()

    return trades


def summary_stats(trades: list[Trade]) -> dict:
    """KPI roll-up used across all downstream reporters."""
    if not trades:
        return {
            "n": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0,
            "avg_r": 0.0, "sum_r": 0.0,
        }
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    n = len(trades)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n,
        "total_pnl": sum(t.net_pnl for t in trades),
        "avg_win": gross_win / max(1, len(wins)),
        "avg_loss": -gross_loss / max(1, len(losses)),
        "profit_factor": gross_win / gross_loss if gross_loss else float("inf"),
        "expectancy": sum(t.net_pnl for t in trades) / n,
        "avg_r": sum(t.r_multiple for t in trades) / n,
        "sum_r": sum(t.r_multiple for t in trades),
    }


__all__ = ["Trade", "load_trades", "summary_stats", "DEFAULT_JOURNAL"]
