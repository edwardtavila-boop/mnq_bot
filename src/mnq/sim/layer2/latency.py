"""[REAL] Trivial deterministic latency model for Layer-2 sim.

Layer-2 is bar-driven, so the concept of latency collapses to
"which bar does the fill land on?". For v0.1 we use the standard
conservative assumption:

    Entry signal fires on bar T close → entry fills on bar T+1 open.

A future enhancement will model limit-order queue + market-fallback
behavior at millisecond resolution; for now a single-bar offset is
enough to make the sim deterministic and not-look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LatencyModel:
    entry_bar_delay: int = 1
    exit_bar_delay: int = 0
