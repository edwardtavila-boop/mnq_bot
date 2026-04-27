"""[REAL] Streaming feature library.

Each feature is a stateful class that ingests `Bar` (or just the needed
source value) one bar at a time and exposes a `value` and a `ready`
flag. Values compute in float64 internally for speed; boundary-crossing
values are tick-quantized elsewhere.

The generators/python_exec generator imports these classes by name.
Step 4's DoD pins behavior via reference-fixture unit tests.
"""

from __future__ import annotations

from mnq.features.atr import ATR
from mnq.features.ema import EMA
from mnq.features.htf import HTFWrapper
from mnq.features.microstructure import (
    BarImbalance,
    BarReturnAutocorrelation,
    LiquidityAbsorption,
    VolumeEntropy,
)
from mnq.features.rma import RMA
from mnq.features.rvol import RelativeVolume
from mnq.features.sma import SMA
from mnq.features.vwap import VWAP

__all__ = [
    "ATR",
    "EMA",
    "HTFWrapper",
    "RMA",
    "SMA",
    "VWAP",
    "BarImbalance",
    "BarReturnAutocorrelation",
    "LiquidityAbsorption",
    "RelativeVolume",
    "VolumeEntropy",
]
