"""[REAL] Strategy spec schema (pydantic v2). The action space of the GA.

Anything not expressible here is not legal except in `experimental: true`
specs, which the agent reads but does not mutate. The promotion path from
experimental → schema is human-mediated: extend this file, then the agent
can play with the new primitive.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

# ---------- features ----------


class _Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    timeframe: str | None = None  # None = primary tf; "5m", "15m", etc. for HTF


class EMA(_Feature):
    type: Literal["ema"] = "ema"
    source: Literal["open", "high", "low", "close", "hl2", "hlc3", "ohlc4"] = "close"
    length: Annotated[int, Field(ge=2, le=400)]


class SMA(_Feature):
    type: Literal["sma"] = "sma"
    source: Literal["open", "high", "low", "close", "hl2", "hlc3", "ohlc4"] = "close"
    length: Annotated[int, Field(ge=2, le=400)]


class RMA(_Feature):  # Wilder's smoothing
    type: Literal["rma"] = "rma"
    source: Literal["open", "high", "low", "close", "hl2", "hlc3", "ohlc4"] = "close"
    length: Annotated[int, Field(ge=2, le=400)]


class ATR(_Feature):
    type: Literal["atr"] = "atr"
    length: Annotated[int, Field(ge=2, le=200)] = 14


class VWAP(_Feature):
    type: Literal["vwap"] = "vwap"
    anchor: Literal["session", "week", "month"] = "session"


class RelativeVolume(_Feature):
    type: Literal["relative_volume"] = "relative_volume"
    length: Annotated[int, Field(ge=5, le=200)] = 20


class CumulativeDelta(_Feature):
    type: Literal["cumulative_delta"] = "cumulative_delta"
    reset: Literal["session", "never"] = "session"
    feed_required: Literal["tradovate_l2"] = "tradovate_l2"


Feature = Annotated[
    EMA | SMA | RMA | ATR | VWAP | RelativeVolume | CumulativeDelta,
    Field(discriminator="type"),
]


# ---------- session / blackout ----------


class SessionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    start: str  # "HH:MM" in spec timezone
    end: str  # "HH:MM" in spec timezone
    enabled: bool = True


class Blackout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: Literal["session_offset", "economic_event"] = "session_offset"
    offset_from_session_start_sec: int | None = None
    offset_from_session_end_sec: int | None = None
    duration_sec: int | None = None
    event: str | None = None
    pre_minutes: int | None = None
    post_minutes: int | None = None


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "America/New_York"
    windows: list[SessionWindow]
    blackouts: list[Blackout] = Field(default_factory=list)


# ---------- entries ----------


class EntrySide(BaseModel):
    """Either an explicit list of conditions, or `mirror_of: long`."""

    model_config = ConfigDict(extra="forbid")
    all_of: list[str] | None = None
    any_of: list[str] | None = None
    n_of: tuple[int, list[str]] | None = None  # (k, conditions)
    mirror_of: Literal["long", "short"] | None = None

    @field_validator("mirror_of")
    @classmethod
    def _exclusive(cls, v: str | None, info: ValidationInfo) -> str | None:
        if v is not None and any(info.data.get(k) for k in ("all_of", "any_of", "n_of")):
            raise ValueError("mirror_of is exclusive with all_of/any_of/n_of")
        return v


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    long: EntrySide
    short: EntrySide


# ---------- exits ----------


class InitialStop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["atr_multiple", "fixed_ticks", "swing_low_high"]
    feature: str | None = None  # e.g., "atr_14"; required for atr_multiple
    multiplier: Decimal | None = None
    ticks: int | None = None
    min_ticks: int = 4
    max_ticks: int = 80


class TakeProfit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["r_multiple", "fixed_ticks", "atr_multiple"]
    value: Decimal | None = None
    feature: str | None = None
    multiplier: Decimal | None = None


class Trailing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mfe_giveback", "atr_chandelier", "fixed_ticks"] = "mfe_giveback"
    activate_at_r: Decimal = Decimal("0.8")
    giveback_fraction: Decimal | None = Decimal("0.4")
    atr_multiplier: Decimal | None = None
    ticks: int | None = None


class Breakeven(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activate_at_r: Decimal
    offset_ticks: int = 0


class Exit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_stop: InitialStop
    take_profit: TakeProfit
    trailing: Trailing | None = None
    breakeven: Breakeven | None = None
    time_stop_bars: int | None = None


# ---------- sizing ----------


class PositionSizing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["fixed_risk", "fixed_contracts"] = "fixed_risk"
    risk_per_trade_usd: Decimal | None = None
    fixed_contracts: int | None = None
    max_contracts: int = 3
    min_contracts: int = 1
    rounding: Literal["floor", "round", "ceil"] = "floor"


# ---------- risk caps ----------


class PerTradeRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_loss_usd: Decimal


class PerSessionRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_loss_usd: Decimal
    max_trades: int
    max_consecutive_losses: int = 99
    cooldown_after_loss_min: int = 0
    cooldown_after_consecutive_losses_min: int = 0


class PerWeekRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_loss_usd: Decimal


class PositionRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_concurrent: int = 1
    no_pyramiding: bool = True
    no_reverse_within_bars: int = 0


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_trade: PerTradeRisk
    per_session: PerSessionRisk
    per_week: PerWeekRisk
    position: PositionRisk


# ---------- execution ----------


class Execution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_type: Literal["market", "limit", "limit_then_market"] = "limit_then_market"
    limit_offset_ticks: int = 0
    market_fallback_ms: int = 500
    cancel_unfilled_after_bars: int = 1
    use_oco_for_brackets: bool = True


# ---------- sim-only models ----------


class SlippageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_ticks: int = 1
    exit_market_ticks: int = 1
    exit_stop_ticks: int = 2
    exit_limit_ticks: int = 0
    rejection_probability: float = 0.005


class CommissionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_contract_per_side_usd: Decimal


# ---------- generators ----------


class PineGen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    purpose: Literal["visualization_and_crosscheck", "execution"] = "visualization_and_crosscheck"
    pine_version: int = 6


class PythonExecGen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    target: Literal["tradovate_ws_v1", "mock_ws"] = "tradovate_ws_v1"


class SimGen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    engine: Literal["builtin"] = "builtin"


class Generators(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pine: PineGen = Field(default_factory=PineGen)
    python_executor: PythonExecGen = Field(default_factory=PythonExecGen)
    sim: SimGen = Field(default_factory=SimGen)


# ---------- timeframes / instrument ----------


class Timeframes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: Literal["1m", "5m", "15m", "1h"] = "1m"
    context: list[Literal["5m", "15m", "1h", "4h", "1d"]] = Field(default_factory=list)
    htf_lookahead_protection: bool = True


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: Literal["MNQ"] = "MNQ"
    exchange: Literal["CME"] = "CME"
    tick_size: Decimal = Decimal("0.25")
    point_value: Decimal = Decimal("2.00")
    contract_months: list[str] = Field(default_factory=lambda: ["H", "M", "U", "Z"])
    roll_method: Literal["calendar_8_days_before_expiry"] = "calendar_8_days_before_expiry"
    data_feed: Literal["tradovate_l1", "tradovate_l2", "tv_chart"] = "tradovate_l1"


# ---------- meta ----------


class StrategyMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    semver: str
    parent_hash: str | None = None
    content_hash: str = ""  # filled by validator/hasher
    created_by: str  # "human:user" | "rl_agent_vX" | "chat:proposal_id"
    created_at: datetime
    tier: Literal["sim", "shadow", "live", "retired"] = "sim"
    experimental: bool = False
    rationale: str = ""


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    training_data: dict[str, object] = Field(default_factory=dict)
    validation: dict[str, object] = Field(default_factory=dict)


# ---------- top-level ----------


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 2
    strategy: StrategyMeta
    instrument: Instrument
    timeframes: Timeframes
    session: Session
    features: list[Feature]
    entry: Entry
    position_sizing: PositionSizing
    exit: Exit
    risk: Risk
    execution: Execution
    slippage_model: SlippageModel = Field(default_factory=SlippageModel)
    commission_model: CommissionModel
    generators: Generators = Field(default_factory=Generators)
    provenance: Provenance = Field(default_factory=Provenance)
