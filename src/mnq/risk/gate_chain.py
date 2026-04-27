"""Pre-trade gate chain.

Lifts the Phase D resilience *reporters* (heartbeat, pre_trade_pause,
trade_governor, correlation_cap, deadman_switch) into a composable
evaluator that vetoes `order_submit` before it reaches the venue.

Each gate is a callable returning :class:`GateResult`. The chain
evaluates gates in order and stops on first veto — callers get the
blocking reason, the gate name, and a structured context dict so
the executor can journal `order.blocked` events with full provenance.

Design notes:
- **Pure-data + stdlib only.** Gates read JSON/SQLite state files
  written by the Phase D scripts. No sockets, no long-running state
  objects, no mutation of the journal. This keeps the chain testable
  in isolation and keeps the order-submit path branch-predictable.
- **Fail-open on missing state.** If a gate's state file doesn't
  exist yet (e.g. first run, brand-new journal), the gate returns
  ALLOW with reason="no-state". The alternative — fail-closed on
  missing state — blocks bootstrapping forever. The deadman switch
  separately enforces "no heartbeat = HOT gate" so missing state
  still gets caught by the positive-signal gate.
- **One chain, many envs.** The default chain is intentionally
  non-configurable; bespoke envs (shadow, tiered-live) build their
  own chain via :func:`build_default_chain` overrides.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of a single gate evaluation."""

    allow: bool
    gate: str
    reason: str
    context: dict[str, object] = field(default_factory=dict)


class Gate(Protocol):
    """A gate is any callable returning GateResult. Signature kept narrow
    on purpose — gates pull their own state; the caller doesn't inject it.
    """

    name: str

    def __call__(self) -> GateResult: ...


@dataclass(frozen=True, slots=True)
class GateChain:
    """Ordered sequence of gates, evaluated short-circuit."""

    gates: tuple[Gate, ...]

    def evaluate(self) -> tuple[bool, list[GateResult]]:
        """Run every gate; short-circuit on first DENY.

        Returns ``(allow, results)`` where ``results`` contains every
        gate that ran (including the denying one) in order.
        """
        results: list[GateResult] = []
        for g in self.gates:
            r = g()
            results.append(r)
            if not r.allow:
                return False, results
        return True, results

    def summary(self) -> dict[str, object]:
        """Snapshot of the chain state for reporting — runs every gate."""
        results: list[GateResult] = [g() for g in self.gates]
        return {
            "generated": datetime.now(tz=UTC).isoformat(),
            "allow_all": all(r.allow for r in results),
            "gates": [
                {
                    "name": r.gate,
                    "allow": r.allow,
                    "reason": r.reason,
                    "context": r.context,
                }
                for r in results
            ],
        }


# ---------------------------------------------------------------------------
# Default gate implementations
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
REPORTS_ROOT = REPO_ROOT / "reports"

HEARTBEAT_PATH = DATA_ROOT / "heartbeat.json"
PRE_TRADE_GATE_PATH = DATA_ROOT / "pre_trade_gate.json"
LOSS_STREAK_PATH = DATA_ROOT / "loss_streak.json"
JOURNAL_PATH = DATA_ROOT / "live_sim" / "journal.sqlite"

# Defaults. Override via env or by building a custom chain.
HEARTBEAT_MAX_AGE_SEC = 300  # 5 minutes
GOVERNOR_MAX_TRADES_TODAY = 8
GOVERNOR_MAX_LOSS_STREAK = 3
GOVERNOR_MAX_DAILY_LOSS = 150.0  # absolute dollars
CORRELATION_MAX_AGG_BETA = 2.0


def _safe_load_json(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def heartbeat_gate(
    path: Path = HEARTBEAT_PATH,
    max_age_sec: int = HEARTBEAT_MAX_AGE_SEC,
) -> GateResult:
    """Deny if heartbeat file is missing or stale."""
    data = _safe_load_json(path)
    if data is None:
        # Fail-open on bootstrap — deadman_switch covers the stale case.
        return GateResult(True, "heartbeat", "no-state")
    ts_raw = data.get("ts")
    if not isinstance(ts_raw, str):
        return GateResult(False, "heartbeat", "malformed-state", {"raw": ts_raw})
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return GateResult(False, "heartbeat", "bad-ts", {"raw": ts_raw})
    age = (datetime.now(tz=UTC) - ts).total_seconds()
    if age > max_age_sec:
        return GateResult(
            False,
            "heartbeat",
            f"stale ({age:.0f}s > {max_age_sec}s)",
            {"age_sec": age},
        )
    return GateResult(True, "heartbeat", "alive", {"age_sec": age})


heartbeat_gate.name = "heartbeat"  # type: ignore[attr-defined]


def pre_trade_pause_gate(path: Path = PRE_TRADE_GATE_PATH) -> GateResult:
    """Deny when the gate JSON is HOT (and not expired)."""
    data = _safe_load_json(path)
    if data is None:
        return GateResult(True, "pre_trade_pause", "no-state")
    state = data.get("state", "COLD")
    if state == "COLD":
        return GateResult(True, "pre_trade_pause", "cold")
    # HOT — check expiry if present.
    expires = data.get("expires")
    if isinstance(expires, str):
        try:
            exp = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if datetime.now(tz=UTC) >= exp:
                return GateResult(True, "pre_trade_pause", "hot-expired")
        except ValueError:
            pass
    reason = data.get("reason", "hot")
    return GateResult(False, "pre_trade_pause", f"HOT: {reason}", {"raw": data})


pre_trade_pause_gate.name = "pre_trade_pause"  # type: ignore[attr-defined]


def _today_trades_from_journal(db: Path) -> tuple[int, int, float]:
    """Return ``(n_trades_today, loss_streak_tail, pnl_today)``.

    Reads event-sourced journal. Pairs the most recent pnl.update
    events against their fills to tally today's PnL.
    """
    if not db.exists():
        return 0, 0, 0.0
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        today = datetime.now(tz=UTC).date().isoformat()
        rows = conn.execute(
            "SELECT ts, payload FROM events WHERE event_type = 'pnl.update' "
            "AND substr(ts,1,10) = ? ORDER BY seq ASC",
            (today,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return 0, 0, 0.0
    trades: list[float] = []
    for row in rows:
        try:
            p = json.loads(row["payload"])
            realized = p.get("realized_delta") or p.get("realized") or p.get("pnl")
            if realized is not None:
                trades.append(float(realized))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    pnl = sum(trades)
    n = len(trades)
    streak = 0
    for t in reversed(trades):
        if t < 0:
            streak += 1
        else:
            break
    return n, streak, pnl


def governor_gate(
    journal: Path = JOURNAL_PATH,
    max_trades: int = GOVERNOR_MAX_TRADES_TODAY,
    max_streak: int = GOVERNOR_MAX_LOSS_STREAK,
    max_daily_loss: float = GOVERNOR_MAX_DAILY_LOSS,
) -> GateResult:
    """Deny if daily trade cap, loss streak, or daily loss limit breached."""
    n, streak, pnl = _today_trades_from_journal(journal)
    ctx = {"trades": n, "streak": streak, "pnl": pnl}
    if n >= max_trades:
        return GateResult(False, "governor", f"trade cap ({n} >= {max_trades})", ctx)
    if streak >= max_streak:
        return GateResult(False, "governor", f"loss streak ({streak} >= {max_streak})", ctx)
    if pnl <= -abs(max_daily_loss):
        return GateResult(False, "governor", f"daily loss (${pnl:.2f})", ctx)
    return GateResult(True, "governor", "ok", ctx)


governor_gate.name = "governor"  # type: ignore[attr-defined]


# Simple static beta book. Matches scripts/correlation_cap.py.
_BETAS = {
    "MNQ": 1.0,
    "MES": 0.78,
    "YM": 0.65,
    "RTY": 0.72,
}


def correlation_gate(
    open_positions: dict[str, int] | None = None,
    new_symbol: str = "MNQ",
    new_qty: int = 1,
    max_agg_beta: float = CORRELATION_MAX_AGG_BETA,
) -> GateResult:
    """Deny if aggregate beta exposure + new order exceeds cap.

    ``open_positions`` is ``{symbol: qty}``. Positive = long, negative = short.
    """
    positions = open_positions or {}
    agg = 0.0
    for sym, qty in positions.items():
        agg += _BETAS.get(sym, 1.0) * qty
    agg += _BETAS.get(new_symbol, 1.0) * new_qty
    ctx = {"agg_beta": agg, "cap": max_agg_beta, "new": f"{new_qty}×{new_symbol}"}
    if abs(agg) > max_agg_beta:
        return GateResult(False, "correlation", f"beta {agg:+.2f} exceeds ±{max_agg_beta}", ctx)
    return GateResult(True, "correlation", "within cap", ctx)


correlation_gate.name = "correlation"  # type: ignore[attr-defined]


def deadman_gate(
    heartbeat_path: Path = HEARTBEAT_PATH,
    pre_trade_path: Path = PRE_TRADE_GATE_PATH,
    cutoff_sec: int = HEARTBEAT_MAX_AGE_SEC * 2,
) -> GateResult:
    """Deny if heartbeat is dead past the cutoff even if pre-trade gate
    hasn't been promoted to HOT yet. This is the *last-chance* guard.
    """
    hb = _safe_load_json(heartbeat_path)
    if hb is None:
        # No heartbeat ever recorded — don't deadman-trigger during bootstrap
        # unless pre_trade_pause is already HOT (handled by its own gate).
        return GateResult(True, "deadman", "no-heartbeat-yet")
    ts_raw = hb.get("ts", "")
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
    except ValueError:
        return GateResult(False, "deadman", "bad-heartbeat-ts", {"raw": ts_raw})
    age = (datetime.now(tz=UTC) - ts).total_seconds()
    if age > cutoff_sec:
        return GateResult(
            False,
            "deadman",
            f"heartbeat age {age:.0f}s > cutoff {cutoff_sec}s",
            {"age_sec": age, "pre_trade": _safe_load_json(pre_trade_path)},
        )
    return GateResult(True, "deadman", "safe", {"age_sec": age})


deadman_gate.name = "deadman"  # type: ignore[attr-defined]


def build_default_chain(
    *,
    open_positions: dict[str, int] | None = None,
    new_symbol: str = "MNQ",
    new_qty: int = 1,
) -> GateChain:
    """Factory for the canonical 5-gate chain used by `executor/orders.py`.

    Order matters: cheap checks first, then stateful journal reads last.
    """

    def _heartbeat() -> GateResult:
        return heartbeat_gate()

    _heartbeat.name = "heartbeat"  # type: ignore[attr-defined]

    def _pre_trade() -> GateResult:
        return pre_trade_pause_gate()

    _pre_trade.name = "pre_trade_pause"  # type: ignore[attr-defined]

    def _deadman() -> GateResult:
        return deadman_gate()

    _deadman.name = "deadman"  # type: ignore[attr-defined]

    def _corr() -> GateResult:
        return correlation_gate(open_positions, new_symbol, new_qty)

    _corr.name = "correlation"  # type: ignore[attr-defined]

    def _gov() -> GateResult:
        return governor_gate()

    _gov.name = "governor"  # type: ignore[attr-defined]

    return GateChain(gates=(_heartbeat, _pre_trade, _deadman, _corr, _gov))
