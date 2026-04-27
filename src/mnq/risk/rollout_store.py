"""[REAL] Persistence layer for ``TieredRollout`` state.

A ``TieredRollout`` lives for the life of a variant in production. Crashing,
restarting, or rolling the process cannot reset the promotion history —
otherwise an operator crash loop would re-allow a previously-demoted variant
back to max size. This module serializes the full state (tier, counters,
event log) to JSON and reloads it on startup.

Design:

    * One JSON file per rollout store; keys are variant names.
    * Atomic write via ``tmpfile → rename`` so a kill -9 mid-save never
      corrupts the file.
    * Decimal + datetime encoded as strings; schema is explicit so a
      human can diff the JSON without losing precision.
    * ``load_all`` returns an empty dict if the file is missing —
      fail-open on read since "no state yet" is the legitimate first-run
      case.

Typical usage::

    store = RolloutStore(REPO_ROOT / "data" / "rollouts.json")
    rollouts = store.load_all()
    # ... drive rollouts forward ...
    store.save_all(rollouts)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .tiered_rollout import (
    DEFAULT_DEMOTION_DRAWDOWN_PCT,
    DEFAULT_HALT_CONSECUTIVE_LOSSES,
    DEFAULT_MAX_LOSING_DAYS,
    DEFAULT_MAX_TIER,
    DEFAULT_MIN_TRADES_AT_TIER,
    DEFAULT_MIN_WINNING_DAYS,
    RolloutState,
    TieredRollout,
    TierEvent,
)

SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# Serialization primitives
# ---------------------------------------------------------------------------
def _encode_event(ev: TierEvent) -> dict[str, Any]:
    d = asdict(ev)
    d["ts"] = ev.ts.isoformat()
    return d


def _decode_event(d: dict[str, Any]) -> TierEvent:
    return TierEvent(
        ts=datetime.fromisoformat(d["ts"]),
        variant=d["variant"],
        event_type=d["event_type"],
        from_tier=int(d["from_tier"]),
        to_tier=int(d["to_tier"]),
        reason=d["reason"],
    )


def dump(rollout: TieredRollout) -> dict[str, Any]:
    """Serialize a ``TieredRollout`` to a plain dict.

    Decimal / datetime / enum values are stringified so the output is
    JSON-safe without custom encoders.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "variant": rollout.variant,
        "config": {
            "max_tier": rollout.max_tier,
            "min_trades_at_tier": rollout.min_trades_at_tier,
            "min_winning_days": rollout.min_winning_days,
            "max_losing_days": rollout.max_losing_days,
            "halt_consecutive_losses": rollout.halt_consecutive_losses,
            "demotion_drawdown_pct": str(rollout.demotion_drawdown_pct),
        },
        "state": rollout.state.value,
        "tier": rollout.tier,
        "counters": {
            "trades_at_tier": rollout._trades_at_tier,
            "pnl_at_tier": str(rollout._pnl_at_tier),
            "consecutive_losses": rollout._consecutive_losses,
            "consecutive_winning_days": rollout._consecutive_winning_days,
            "consecutive_losing_days": rollout._consecutive_losing_days,
            "tier_peak_equity": str(rollout._tier_peak_equity),
            "tier_equity": str(rollout._tier_equity),
        },
        "event_log": [_encode_event(ev) for ev in rollout.event_log()],
    }


def load(d: dict[str, Any]) -> TieredRollout:
    """Rebuild a ``TieredRollout`` from a dict produced by :func:`dump`.

    Unknown keys are ignored; missing keys fall back to dataclass defaults
    so older JSON files can still be loaded after a schema bump.
    """
    cfg = d.get("config", {})
    r = TieredRollout(
        variant=d["variant"],
        max_tier=int(cfg.get("max_tier", DEFAULT_MAX_TIER)),
        min_trades_at_tier=int(cfg.get("min_trades_at_tier", DEFAULT_MIN_TRADES_AT_TIER)),
        min_winning_days=int(cfg.get("min_winning_days", DEFAULT_MIN_WINNING_DAYS)),
        max_losing_days=int(cfg.get("max_losing_days", DEFAULT_MAX_LOSING_DAYS)),
        halt_consecutive_losses=int(
            cfg.get("halt_consecutive_losses", DEFAULT_HALT_CONSECUTIVE_LOSSES)
        ),
        demotion_drawdown_pct=Decimal(
            str(cfg.get("demotion_drawdown_pct", DEFAULT_DEMOTION_DRAWDOWN_PCT))
        ),
    )
    r.state = RolloutState(d.get("state", RolloutState.ACTIVE.value))
    r.tier = int(d.get("tier", 0))
    counters = d.get("counters", {})
    r._trades_at_tier = int(counters.get("trades_at_tier", 0))
    r._pnl_at_tier = Decimal(str(counters.get("pnl_at_tier", "0")))
    r._consecutive_losses = int(counters.get("consecutive_losses", 0))
    r._consecutive_winning_days = int(counters.get("consecutive_winning_days", 0))
    r._consecutive_losing_days = int(counters.get("consecutive_losing_days", 0))
    r._tier_peak_equity = Decimal(str(counters.get("tier_peak_equity", "0")))
    r._tier_equity = Decimal(str(counters.get("tier_equity", "0")))
    for ev_dict in d.get("event_log", []):
        r._event_log.append(_decode_event(ev_dict))
    return r


# ---------------------------------------------------------------------------
# File-backed store
# ---------------------------------------------------------------------------
class RolloutStore:
    """JSON file with one entry per variant.

    The whole-file format is::

        {
          "schema_version": 1,
          "variants": {"orb_only_pm30": {...}, "orb_sweep_pm30": {...}}
        }
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # -- whole-file I/O --------------------------------------------------
    def load_all(self) -> dict[str, TieredRollout]:
        """Return all stored rollouts; empty dict if the file is missing."""
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        variants = raw.get("variants", {})
        return {name: load(payload) for name, payload in variants.items()}

    def save_all(self, rollouts: dict[str, TieredRollout]) -> None:
        """Atomically write every rollout to disk.

        Uses a temp file + os.replace so a crash mid-write leaves the
        previous state intact (no torn JSON).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "variants": {name: dump(r) for name, r in rollouts.items()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    # -- single-variant convenience -------------------------------------
    def load(self, variant: str) -> TieredRollout | None:
        return self.load_all().get(variant)

    def save(self, rollout: TieredRollout) -> None:
        existing = self.load_all()
        existing[rollout.variant] = rollout
        self.save_all(existing)

    def variants(self) -> list[str]:
        return sorted(self.load_all().keys())


__all__ = [
    "SCHEMA_VERSION",
    "RolloutStore",
    "dump",
    "load",
]
