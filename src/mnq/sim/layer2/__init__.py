"""[REAL] Layer-2 event-driven simulator.

Public surface:

    Layer2Engine(spec, strategy, *, seed=0)  — bar-driven event loop
    SimulatedFill                            — result record
    TradeLedger                              — sequence of round-trip trades
    run_layer2(spec, strategy, bars, seed)   — convenience wrapper
"""
from __future__ import annotations

from mnq.sim.layer2.engine import Layer2Engine, TradeLedger, TradeRecord, run_layer2
from mnq.sim.layer2.fills import SimulatedFill, simulate_exit_within_bar
from mnq.sim.layer2.latency import LatencyModel

__all__ = [
    "Layer2Engine",
    "LatencyModel",
    "SimulatedFill",
    "TradeLedger",
    "TradeRecord",
    "run_layer2",
    "simulate_exit_within_bar",
]
