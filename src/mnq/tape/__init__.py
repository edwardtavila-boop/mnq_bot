"""Real-tape adapters for live-like replay.

The ``tape`` package consumes the Databento parquet/CSV cache and yields
``Bar`` objects (from :mod:`mnq.core.types`) one at a time, in
chronological order. This is what feeds the live runtime
(``scripts/run_eta_live.py``) when the operator is doing a paper-mode
soak against historical tape.

Live-feed adapters (TradingView webhook, broker-native streams) are
*not* in this package — they belong with the venue layer. This package
is strictly historical replay.
"""

from __future__ import annotations

from mnq.tape.databento_tape import (
    DEFAULT_DATABENTO_5M,
    iter_databento_bars,
    load_databento_bars,
)

__all__ = [
    "DEFAULT_DATABENTO_5M",
    "iter_databento_bars",
    "load_databento_bars",
]
