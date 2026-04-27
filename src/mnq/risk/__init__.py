"""Risk module — pre-trade gate chain composing Phase D resilience layers.

Exports :class:`GateChain`, :class:`GateResult`, and the default
:func:`build_default_chain` that wires heartbeat, pre-trade pause,
trade governor, correlation cap, and deadman switch in order.

Also exports the per-regime :class:`HeatBudget` (Phase 5) for
concurrency caps and heat-based position limiting.
"""

from .gate_chain import (
    Gate,
    GateChain,
    GateResult,
    build_default_chain,
)
from .heat_budget import (
    CanonicalRegime,
    HeatBudget,
    HeatCheckResult,
    Position,
    RegimeHeatConfig,
    heat_budget_gate,
)
from .rollout_store import RolloutStore
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

__all__ = [
    "Gate",
    "GateChain",
    "GateResult",
    "build_default_chain",
    "CanonicalRegime",
    "HeatBudget",
    "HeatCheckResult",
    "Position",
    "RegimeHeatConfig",
    "heat_budget_gate",
    "DEFAULT_DEMOTION_DRAWDOWN_PCT",
    "DEFAULT_HALT_CONSECUTIVE_LOSSES",
    "DEFAULT_MAX_LOSING_DAYS",
    "DEFAULT_MAX_TIER",
    "DEFAULT_MIN_TRADES_AT_TIER",
    "DEFAULT_MIN_WINNING_DAYS",
    "RolloutState",
    "TierEvent",
    "TieredRollout",
    "RolloutStore",
]
