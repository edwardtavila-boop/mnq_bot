#!/usr/bin/env python3
"""Tier driver — fold trade outcomes into each variant's rollout state.

The state machine in ``mnq.risk.tiered_rollout`` only advances when its
mutators are called. This driver is the glue between the trade tape and
that state machine:

    1. Load the ship manifest (so we only drive *shippable* variants).
    2. Load existing rollouts from ``data/rollouts.json`` — or initialize
       fresh ``TieredRollout.initial(name)`` for new shippable variants.
    3. Replay every trade record in chronological order through
       ``rollout.record_trade(pnl, closed_at)``.
    4. At session-date boundaries, fold ``rollout.record_eod(...)``.
    5. Atomically persist updated state via ``RolloutStore.save_all``.

Trade record schema (JSON list of dicts, sorted by ``closed_at``)::

    [
      {"variant": "orb_only_pm30", "pnl": "12.50",
       "closed_at": "2026-04-01T14:30:00+00:00"},
      {"variant": "orb_sweep_pm30", "pnl": "-5.25",
       "closed_at": "2026-04-01T15:02:00+00:00"},
      ...
    ]

The driver is purely a consumer of state; it never *originates* trades.
That keeps the invariant: ``rollouts.json`` is a deterministic function
of ``(ship_manifest, trades_json)``.

Usage::

    python scripts/tier_driver.py --trades reports/recent_trades.json \\
        --rollouts data/rollouts.json
    python scripts/tier_driver.py --trades t.json --rollouts r.json \\
        --variants orb_only_pm30,orb_sweep_pm30   # subset

CI dry-run (no files written)::

    python scripts/tier_driver.py --trades t.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
for p in (SRC,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mnq.gauntlet.ship_manifest import ShipManifest, ShipManifestMissingError  # noqa: E402
from mnq.risk.rollout_store import RolloutStore  # noqa: E402
from mnq.risk.tiered_rollout import TieredRollout  # noqa: E402

DEFAULT_ROLLOUTS_PATH = REPO_ROOT / "data" / "rollouts.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TradeRecord:
    """One closed trade fed into the driver."""

    variant: str
    pnl: Decimal
    closed_at: datetime

    @classmethod
    def from_dict(cls, d: dict) -> TradeRecord:
        ts = d["closed_at"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return cls(
            variant=str(d["variant"]),
            pnl=Decimal(str(d["pnl"])),
            closed_at=ts,
        )


# ---------------------------------------------------------------------------
# Core driver
# ---------------------------------------------------------------------------
def drive_rollouts(
    *,
    manifest: ShipManifest,
    trades: list[TradeRecord],
    existing: dict[str, TieredRollout] | None = None,
    variants: list[str] | None = None,
) -> dict[str, TieredRollout]:
    """Fold ``trades`` into per-variant rollouts.

    * A new ``TieredRollout.initial(name)`` is auto-created for any
      shippable variant that has no stored state yet.
    * Trades for non-shippable (FAIL / KILL / unknown) variants are
      silently skipped — the ship manifest is the source of truth for
      *what* is eligible for promotion.
    * Trades are replayed in chronological order regardless of their
      order in the input list.
    * End-of-day is folded the moment the trade tape crosses a session
      date boundary (UTC date of ``closed_at``).
    """
    shippable = set(manifest.shippable_variants())
    if variants is not None:
        shippable &= set(variants)

    rollouts: dict[str, TieredRollout] = dict(existing or {})
    for name in shippable:
        if name not in rollouts:
            rollouts[name] = TieredRollout.initial(name)

    # Sort trades by closed_at so day-boundary detection is unambiguous.
    ordered = sorted(
        (t for t in trades if t.variant in shippable),
        key=lambda t: t.closed_at,
    )

    # Group trades by (variant, session_date) so EOD folds per-variant.
    # We interleave record_trade() in chronological order but fire EOD
    # when we first see a trade for a variant on a strictly later date.
    last_trade_date: dict[str, date] = {}
    pending_day_pnl: dict[str, Decimal] = {}

    def _fold_eod(variant: str, day: date, closed_at: datetime) -> None:
        """Trigger record_eod for ``variant`` at end of ``day``."""
        pnl = pending_day_pnl.pop(variant, Decimal(0))
        r = rollouts[variant]
        r.record_eod(day_end_pnl=pnl, day=day, closed_at=closed_at)

    for t in ordered:
        r = rollouts[t.variant]
        day = t.closed_at.date()

        prev_day = last_trade_date.get(t.variant)
        if prev_day is not None and day != prev_day:
            # We just crossed a session boundary for this variant — fold EOD
            # for the prior day before recording today's trade.
            # closed_at for the EOD event uses the new trade's timestamp
            # (journal convention: the EOD is stamped the moment it's
            # observed, not backdated).
            _fold_eod(t.variant, prev_day, t.closed_at)

        r.record_trade(t.pnl, t.closed_at)
        pending_day_pnl[t.variant] = pending_day_pnl.get(t.variant, Decimal(0)) + t.pnl
        last_trade_date[t.variant] = day

    # Flush every remaining pending day at the last-seen timestamp so the
    # state matches "we ran through all trades we have".
    if ordered:
        last_ts = ordered[-1].closed_at
        for variant, day in list(last_trade_date.items()):
            _fold_eod(variant, day, last_ts)

    return rollouts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_trades(path: Path) -> list[TradeRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"trades file {path} must be a JSON list")
    return [TradeRecord.from_dict(d) for d in data]


def _parse_variant_list(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [v.strip() for v in raw.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Drive variant rollouts from trade records.")
    p.add_argument(
        "--trades", type=Path, required=True, help="JSON file with trade records to fold in."
    )
    p.add_argument(
        "--rollouts",
        type=Path,
        default=DEFAULT_ROLLOUTS_PATH,
        help=f"Rollout store path (default {DEFAULT_ROLLOUTS_PATH}).",
    )
    p.add_argument("--manifest", type=Path, default=None, help="Override ship manifest path.")
    p.add_argument("--variants", type=str, default=None, help="Comma-separated variant subset.")
    p.add_argument(
        "--reset", action="store_true", help="Ignore existing rollout state and start fresh."
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Compute new state but do not write to disk."
    )
    args = p.parse_args(argv)

    try:
        manifest = (
            ShipManifest.from_default_path()
            if args.manifest is None
            else ShipManifest.from_path(args.manifest)
        )
    except ShipManifestMissingError as e:
        print(f"tier_driver: ship manifest missing — {e}", file=sys.stderr)
        print("run `python scripts/edge_forensics.py` first", file=sys.stderr)
        return 2

    trades = _load_trades(args.trades)

    store = RolloutStore(args.rollouts)
    existing = {} if args.reset else store.load_all()

    updated = drive_rollouts(
        manifest=manifest,
        trades=trades,
        existing=existing,
        variants=_parse_variant_list(args.variants),
    )

    if not args.dry_run:
        store.save_all(updated)

    # Print a small summary line for the operator.
    lines = []
    for name in sorted(updated):
        r = updated[name]
        lines.append(
            f"  {name}: state={r.state.value} tier={r.tier} "
            f"allowed_qty={r.allowed_qty()} events={len(r.event_log())}"
        )
    header = f"tier_driver: {len(updated)} variant(s) {'(dry-run)' if args.dry_run else 'saved'}"
    print(header)
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
