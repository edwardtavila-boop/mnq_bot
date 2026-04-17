"""Per-regime heat budget and concurrency limiter — Phase 5 completion.

Heat is a normalized risk metric: each open position consumes heat based on
its notional exposure, regime volatility, and correlation to existing book.
When aggregate heat exceeds the regime's budget, no new positions are allowed.

Design:
  - Each of the 9 canonical regimes has its own heat budget (0.0–1.0 scale)
  - TRANSITION regime gets the tightest budget (0.3)
  - Dead-Zone regime budget is 0.0 (no trading)
  - Concurrency cap: max simultaneous positions per regime
  - Integrates as a Gate in gate_chain.py

Heat formula per position:
    heat_i = (notional_i / account_equity) * vol_multiplier * correlation_factor

Where:
    notional_i = qty * price * point_value
    vol_multiplier = current_atr / baseline_atr (> 1 in high vol)
    correlation_factor = 1.0 for first position, scales up with correlated adds

Usage:
    budget = HeatBudget(config, regime="low-vol-trend")
    result = budget.check(new_position, existing_positions, account_equity)
    if not result.allow:
        # reject the trade
        pass

Gate chain integration:
    chain = build_default_chain(...)  # includes heat_budget_gate
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from mnq.risk.gate_chain import GateResult


# ── Canonical regimes (matches firm/regime.py 9 + TRANSITION) ──────────

class CanonicalRegime(str, Enum):
    LOW_VOL_TREND = "low-vol-trend"
    LOW_VOL_RANGE = "low-vol-range"
    LOW_VOL_REVERSAL = "low-vol-reversal"
    HIGH_VOL_TREND = "high-vol-trend"
    HIGH_VOL_RANGE = "high-vol-range"
    HIGH_VOL_REVERSAL = "high-vol-reversal"
    CRASH = "crash"
    EUPHORIA = "euphoria"
    DEAD_ZONE = "dead-zone"
    TRANSITION = "transition"


@dataclass(frozen=True)
class RegimeHeatConfig:
    """Heat parameters for a single regime."""

    max_heat: float          # 0.0–1.0, max aggregate heat allowed
    max_concurrent: int      # Max simultaneous open positions
    vol_multiplier_cap: float  # Cap on vol_multiplier to prevent extreme scaling
    sizing_fraction: float   # Kelly fraction cap (half-Kelly default)
    notes: str = ""


# Default regime configs — tuned conservatively for MNQ $5k account
DEFAULT_REGIME_CONFIGS: dict[CanonicalRegime, RegimeHeatConfig] = {
    CanonicalRegime.LOW_VOL_TREND: RegimeHeatConfig(
        max_heat=0.8, max_concurrent=3, vol_multiplier_cap=1.5,
        sizing_fraction=0.5, notes="Best regime — full budget"
    ),
    CanonicalRegime.LOW_VOL_RANGE: RegimeHeatConfig(
        max_heat=0.6, max_concurrent=2, vol_multiplier_cap=1.3,
        sizing_fraction=0.4, notes="Good for mean-reversion setups"
    ),
    CanonicalRegime.LOW_VOL_REVERSAL: RegimeHeatConfig(
        max_heat=0.5, max_concurrent=2, vol_multiplier_cap=1.3,
        sizing_fraction=0.35, notes="Reversal = higher uncertainty"
    ),
    CanonicalRegime.HIGH_VOL_TREND: RegimeHeatConfig(
        max_heat=0.6, max_concurrent=2, vol_multiplier_cap=2.0,
        sizing_fraction=0.35, notes="Wider stops needed, fewer positions"
    ),
    CanonicalRegime.HIGH_VOL_RANGE: RegimeHeatConfig(
        max_heat=0.4, max_concurrent=1, vol_multiplier_cap=2.0,
        sizing_fraction=0.25, notes="Choppy — tight budget"
    ),
    CanonicalRegime.HIGH_VOL_REVERSAL: RegimeHeatConfig(
        max_heat=0.4, max_concurrent=1, vol_multiplier_cap=2.5,
        sizing_fraction=0.25, notes="High risk turning points"
    ),
    CanonicalRegime.CRASH: RegimeHeatConfig(
        max_heat=0.2, max_concurrent=1, vol_multiplier_cap=3.0,
        sizing_fraction=0.15, notes="Defensive only — small counter-trend or flat"
    ),
    CanonicalRegime.EUPHORIA: RegimeHeatConfig(
        max_heat=0.3, max_concurrent=1, vol_multiplier_cap=2.0,
        sizing_fraction=0.2, notes="Blow-off risk — tight sizing"
    ),
    CanonicalRegime.DEAD_ZONE: RegimeHeatConfig(
        max_heat=0.0, max_concurrent=0, vol_multiplier_cap=1.0,
        sizing_fraction=0.0, notes="NO TRADING — liquidity desert"
    ),
    CanonicalRegime.TRANSITION: RegimeHeatConfig(
        max_heat=0.3, max_concurrent=1, vol_multiplier_cap=1.5,
        sizing_fraction=0.2, notes="Regime changing — minimal exposure"
    ),
}


@dataclass(frozen=True)
class Position:
    """Represents an open position for heat calculation."""

    symbol: str
    qty: int                 # Positive = long, negative = short
    entry_price: float
    point_value: float = 5.0  # MNQ = $5 per point
    current_atr: float = 0.0  # Current ATR in points
    baseline_atr: float = 1.0  # Historical average ATR


@dataclass(frozen=True)
class HeatCheckResult:
    """Result of a heat budget check."""

    allow: bool
    current_heat: float
    new_heat: float          # Heat if position is added
    budget: float            # Max heat for this regime
    heat_remaining: float
    current_positions: int
    max_positions: int
    regime: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


# ── Symbol correlation matrix (simplified) ────────────────────────────

# Correlation coefficients between index futures.
# Used to scale heat when adding correlated positions.
_CORRELATIONS: dict[tuple[str, str], float] = {
    ("MNQ", "MES"): 0.92,
    ("MNQ", "YM"): 0.78,
    ("MNQ", "RTY"): 0.72,
    ("MES", "YM"): 0.82,
    ("MES", "RTY"): 0.76,
    ("YM", "RTY"): 0.68,
}


def _get_correlation(sym_a: str, sym_b: str) -> float:
    """Symmetric lookup; same-symbol = 1.0, unknown = 0.5."""
    if sym_a == sym_b:
        return 1.0
    key = tuple(sorted([sym_a, sym_b]))
    return _CORRELATIONS.get(key, 0.5)


def compute_position_heat(
    pos: Position,
    account_equity: float,
    vol_multiplier_cap: float = 2.0,
) -> float:
    """Compute heat contribution of a single position.

    Returns a value in [0, ~1] representing the fraction of risk budget consumed.
    """
    if account_equity <= 0:
        return 1.0  # Infinite heat if no equity

    notional = abs(pos.qty) * pos.entry_price * pos.point_value
    base_heat = notional / account_equity

    # Vol multiplier: how much hotter is current vol vs baseline?
    if pos.baseline_atr > 0 and pos.current_atr > 0:
        vol_mult = min(pos.current_atr / pos.baseline_atr, vol_multiplier_cap)
    else:
        vol_mult = 1.0

    return base_heat * vol_mult


def compute_aggregate_heat(
    positions: list[Position],
    account_equity: float,
    vol_multiplier_cap: float = 2.0,
) -> float:
    """Compute total heat of all open positions with correlation adjustment.

    Correlated positions amplify heat beyond simple sum.
    """
    if not positions:
        return 0.0

    # Individual heats
    heats = [
        compute_position_heat(p, account_equity, vol_multiplier_cap)
        for p in positions
    ]

    # Sum of individual heats (base case)
    base_heat = sum(heats)

    # Correlation penalty: for each pair, add correlation * heat_i * heat_j
    n = len(positions)
    corr_penalty = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            rho = _get_correlation(positions[i].symbol, positions[j].symbol)
            corr_penalty += rho * heats[i] * heats[j]

    return base_heat + corr_penalty


class HeatBudget:
    """Per-regime heat budget evaluator."""

    def __init__(
        self,
        regime: str | CanonicalRegime = CanonicalRegime.TRANSITION,
        configs: dict[CanonicalRegime, RegimeHeatConfig] | None = None,
        state_path: Path | None = None,
    ):
        self.configs = configs or DEFAULT_REGIME_CONFIGS
        self._set_regime(regime)
        repo_root = Path(__file__).resolve().parents[3]
        self.state_path = state_path or (repo_root / "data" / "heat_state.json")

    def _set_regime(self, regime: str | CanonicalRegime) -> None:
        """Set current regime and look up its config."""
        if isinstance(regime, str):
            try:
                self.regime = CanonicalRegime(regime)
            except ValueError:
                # Unknown regime → default to TRANSITION (conservative)
                self.regime = CanonicalRegime.TRANSITION
        else:
            self.regime = regime
        self.config = self.configs.get(self.regime, self.configs[CanonicalRegime.TRANSITION])

    def update_regime(self, regime: str | CanonicalRegime) -> None:
        """Update the active regime (called when regime classifier fires)."""
        self._set_regime(regime)

    def check(
        self,
        new_position: Position,
        existing_positions: list[Position],
        account_equity: float,
    ) -> HeatCheckResult:
        """Check whether adding new_position stays within the heat budget.

        Args:
            new_position: The position being proposed.
            existing_positions: Currently open positions.
            account_equity: Current account equity in dollars.

        Returns:
            HeatCheckResult with allow/deny and full context.
        """
        cfg = self.config
        n_existing = len(existing_positions)

        # Dead-zone: no trading at all
        if cfg.max_heat <= 0:
            return HeatCheckResult(
                allow=False,
                current_heat=0.0,
                new_heat=0.0,
                budget=cfg.max_heat,
                heat_remaining=0.0,
                current_positions=n_existing,
                max_positions=cfg.max_concurrent,
                regime=self.regime.value,
                reason=f"Dead-zone regime: no trading allowed",
            )

        # Concurrency check
        if n_existing >= cfg.max_concurrent:
            current_heat = compute_aggregate_heat(
                existing_positions, account_equity, cfg.vol_multiplier_cap
            )
            return HeatCheckResult(
                allow=False,
                current_heat=current_heat,
                new_heat=current_heat,
                budget=cfg.max_heat,
                heat_remaining=max(0, cfg.max_heat - current_heat),
                current_positions=n_existing,
                max_positions=cfg.max_concurrent,
                regime=self.regime.value,
                reason=f"Concurrency cap: {n_existing} >= {cfg.max_concurrent}",
            )

        # Heat calculation
        current_heat = compute_aggregate_heat(
            existing_positions, account_equity, cfg.vol_multiplier_cap
        )
        proposed_book = existing_positions + [new_position]
        new_heat = compute_aggregate_heat(
            proposed_book, account_equity, cfg.vol_multiplier_cap
        )

        heat_remaining = max(0, cfg.max_heat - new_heat)

        if new_heat > cfg.max_heat:
            return HeatCheckResult(
                allow=False,
                current_heat=current_heat,
                new_heat=new_heat,
                budget=cfg.max_heat,
                heat_remaining=0.0,
                current_positions=n_existing,
                max_positions=cfg.max_concurrent,
                regime=self.regime.value,
                reason=f"Heat budget exceeded: {new_heat:.3f} > {cfg.max_heat}",
                details={
                    "position_heat": compute_position_heat(
                        new_position, account_equity, cfg.vol_multiplier_cap
                    ),
                    "correlation_impact": new_heat - current_heat - compute_position_heat(
                        new_position, account_equity, cfg.vol_multiplier_cap
                    ),
                },
            )

        return HeatCheckResult(
            allow=True,
            current_heat=current_heat,
            new_heat=new_heat,
            budget=cfg.max_heat,
            heat_remaining=heat_remaining,
            current_positions=n_existing,
            max_positions=cfg.max_concurrent,
            regime=self.regime.value,
            reason="within budget",
            details={
                "position_heat": compute_position_heat(
                    new_position, account_equity, cfg.vol_multiplier_cap
                ),
                "utilization_pct": round(new_heat / cfg.max_heat * 100, 1),
            },
        )

    def save_state(
        self,
        existing_positions: list[Position],
        account_equity: float,
    ) -> None:
        """Persist current heat state for dashboard consumption."""
        current_heat = compute_aggregate_heat(
            existing_positions, account_equity, self.config.vol_multiplier_cap
        )
        state = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "regime": self.regime.value,
            "current_heat": round(current_heat, 4),
            "budget": self.config.max_heat,
            "utilization_pct": round(
                current_heat / self.config.max_heat * 100, 1
            ) if self.config.max_heat > 0 else 0.0,
            "positions": len(existing_positions),
            "max_concurrent": self.config.max_concurrent,
            "sizing_fraction": self.config.sizing_fraction,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2))


# ── Gate chain integration ─────────────────────────────────────────────

def heat_budget_gate(
    budget: HeatBudget,
    new_position: Position,
    existing_positions: list[Position],
    account_equity: float,
) -> GateResult:
    """Gate-chain-compatible wrapper around HeatBudget.check().

    Returns GateResult for integration with gate_chain.GateChain.
    """
    result = budget.check(new_position, existing_positions, account_equity)

    return GateResult(
        allow=result.allow,
        gate="heat_budget",
        reason=result.reason,
        context={
            "current_heat": round(result.current_heat, 4),
            "new_heat": round(result.new_heat, 4),
            "budget": result.budget,
            "regime": result.regime,
            "positions": f"{result.current_positions}/{result.max_positions}",
        },
    )


heat_budget_gate.name = "heat_budget"  # type: ignore[attr-defined]
