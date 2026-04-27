"""Statistical utilities for promotion-gate artifact generation.

Currently exposes:
    block_bootstrap_ci -- block bootstrap CI95 of per-trade R-multiples
                          (gate 3 input)

Future modules will add: walk-forward CI, DSR / PSR (gate 4), DOW
placebo (gate 7), etc.
"""

from __future__ import annotations

from mnq.stats.block_bootstrap import block_bootstrap_ci

__all__ = ["block_bootstrap_ci"]
