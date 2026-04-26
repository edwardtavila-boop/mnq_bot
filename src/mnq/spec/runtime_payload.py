"""[REAL] Build a Firm-shaped ``spec_payload`` for the live runtime.

The runtime's per-bar Firm review (B4 closure, v0.2.6) needs a payload
that the six-stage agents can evaluate. v0.2.6 used a hardcoded stub
(``sample_size=100, expected_expectancy_r=0.5, ...``) which made every
PM verdict uncalibrated.

This module is the v0.2.7 closure: it produces a **real** spec_payload
from:

  1. The variant's :class:`StrategyConfig` from
     ``scripts/strategy_v2.py`` (entry/stop/target knobs)
  2. The baseline strategy yaml at
     ``specs/strategies/v0_1_baseline.yaml`` (risk caps, sessions,
     instrument)
  3. A cached backtest summary at ``data/backtest_real_daily.json``
     (per-day P&L per variant) -- supplies sample_size +
     expected_expectancy_r without needing to re-run the backtest

The intent is light: this is called once per runtime startup, NOT
per-bar. The cached backtest stats are stale by construction (last
run timestamp is in the file) but are far better than the stub
constants and don't blow runtime startup time.

Fail-open: every step has a sensible default if its source is missing.
A runtime should boot even if no backtest has been run yet (it just
gets a payload labelled ``provenance=stub``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mnq.core.paths import REPO_ROOT
from mnq.core.types import MNQ_POINT_VALUE, MNQ_TICK_SIZE

# Canonical baseline yaml -- risk caps + session config live here.
BASELINE_YAML: Path = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"

# Cached per-day P&L per variant (produced by scripts/backtest_real_v3.py).
BACKTEST_DAILY_JSON: Path = REPO_ROOT / "data" / "backtest_real_daily.json"

# Estimated trades per day for sample-size derivation. Real value depends on
# the variant; this is a calibration constant for the proxy "n_trades".
# Override per-variant in v0.2.8 once we journal trade counts directly.
TRADES_PER_DAY_PROXY = 2


def _load_variant_config(variant_name: str) -> Any | None:
    """Resolve a variant by name from ``scripts/strategy_v2.VARIANTS``.

    Returns the ``StrategyConfig`` dataclass instance or None if the
    variant doesn't exist.
    """
    import sys
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from strategy_v2 import VARIANTS  # type: ignore
    except ImportError:
        return None
    for cfg in VARIANTS:
        if cfg.name == variant_name:
            return cfg
    return None


def _load_baseline_spec() -> Any | None:
    """Load + validate the baseline yaml. Returns None if missing/broken."""
    if not BASELINE_YAML.exists():
        return None
    try:
        from mnq.spec.loader import load_spec
    except ImportError:
        return None
    try:
        return load_spec(BASELINE_YAML)
    except Exception:  # noqa: BLE001 -- spec validation chains are diverse
        return None


def _load_cached_backtest(variant_name: str) -> dict[str, float] | None:
    """Pull per-day P&L for ``variant_name`` from the cached backtest JSON.

    Returns the per-date P&L dict, or None if the file is missing or
    the variant isn't present.
    """
    if not BACKTEST_DAILY_JSON.exists():
        return None
    try:
        data = json.loads(BACKTEST_DAILY_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    daily = data.get(variant_name)
    if not isinstance(daily, dict):
        return None
    # Coerce to {str: float}
    return {k: float(v) for k, v in daily.items() if isinstance(v, (int, float))}


def _derive_sample_stats(
    cfg: Any,
    daily_pnl: dict[str, float] | None,
) -> tuple[int, float, float]:
    """From per-day P&L, derive (sample_size, expectancy_r, oos_degradation_pct).

    Returns three sentinel values when daily_pnl is None:
      sample_size = 0, expectancy_r = 0.0, oos_degradation_pct = 100.0

    The 100.0% sentinel for OOS degradation is intentionally pessimistic
    so the Firm RedTeam stage flags un-validated strategies until a real
    walk-forward report lands.
    """
    if not daily_pnl:
        return 0, 0.0, 100.0
    n_days = len(daily_pnl)
    sample_size = max(n_days * TRADES_PER_DAY_PROXY, 1)
    total_pnl = sum(daily_pnl.values())
    # Risk dollars per trade = risk_ticks * tick_value
    risk_ticks = float(getattr(cfg, "risk_ticks", 0)) if cfg is not None else 0.0
    risk_dollars = (
        risk_ticks * float(MNQ_TICK_SIZE) * float(MNQ_POINT_VALUE)
    ) if risk_ticks > 0 else 0.0
    expectancy_r = (
        (total_pnl / sample_size) / risk_dollars if risk_dollars > 0 else 0.0
    )
    # OOS degradation proxy: best-day vs worst-day spread
    pnls = list(daily_pnl.values())
    if pnls:
        best = max(pnls)
        worst = min(pnls)
        oos_deg = (
            max(0.0, (best - worst) / best) * 100.0 if best > 0 else 100.0
        )
    else:
        oos_deg = 100.0
    return sample_size, float(expectancy_r), float(oos_deg)


def _approved_regimes(daily_pnl: dict[str, float] | None) -> list[str]:
    """Approved regimes proxy: bin days by P&L sign.

    Without a per-bar regime tag we can't do better than a coarse split.
    Days with positive P&L map to ``normal_vol_trend`` (the strategy's
    sweet spot); negative-P&L days don't get an approval label.
    """
    if not daily_pnl:
        return []
    pos_days = [d for d, v in daily_pnl.items() if v > 0]
    if pos_days:
        return ["normal_vol_trend"]
    return []


def _entry_logic_str(cfg: Any) -> str:
    """Render a short human-readable entry-logic string from a StrategyConfig."""
    if cfg is None:
        return "unknown variant"
    parts = []
    fast = getattr(cfg, "ema_fast", None)
    slow = getattr(cfg, "ema_slow", None)
    if fast is not None and slow is not None:
        parts.append(f"EMA{fast}/EMA{slow} cross")
    cmm = getattr(cfg, "cross_magnitude_min", None)
    if cmm is not None:
        parts.append(f"min spread {float(cmm):.2f} pts")
    vfs = getattr(cfg, "vol_filter_stdev_max", None)
    if vfs is not None and float(vfs) > 0:
        parts.append(f"vol filter sigma<={vfs}")
    vhp = getattr(cfg, "vol_hard_pause_stdev", None)
    if vhp is not None and float(vhp) > 0:
        parts.append(f"hard pause sigma>{vhp}")
    of = getattr(cfg, "orderflow_proxy_min", None)
    if of is not None:
        parts.append(f"orderflow>={float(of):.2f}")
    return ", ".join(parts) if parts else "default rules"


def _stop_logic_str(cfg: Any) -> str:
    if cfg is None:
        return "default stop"
    rt = getattr(cfg, "risk_ticks", None)
    ts = getattr(cfg, "time_stop_bars", None)
    if rt is not None and ts is not None:
        return f"{rt}-tick hard stop; time stop {ts} bars"
    if rt is not None:
        return f"{rt}-tick hard stop"
    return "default stop"


def _target_logic_str(cfg: Any) -> str:
    if cfg is None:
        return "default target"
    rr = getattr(cfg, "rr", None)
    if rr is not None:
        return f"{float(rr)}R fixed target"
    return "default target"


def _approved_sessions(spec: Any) -> list[str]:
    """Pull approved sessions from the baseline spec.session field."""
    if spec is None:
        return ["RTH"]
    session = getattr(spec, "session", None)
    if session is None:
        return ["RTH"]
    # The Session model carries an `allow` / `phase` / similar field
    # depending on schema version. Cheap inspection: probe known fields.
    for attr in ("approved_sessions", "phases", "allow", "phase"):
        val = getattr(session, attr, None)
        if isinstance(val, (list, tuple)) and val:
            return [str(v) for v in val]
        if isinstance(val, str) and val:
            return [val]
    return ["RTH"]


def _dd_kill_switch_r(cfg: Any, spec: Any) -> float:
    """Derive dd_kill_switch_r from the baseline yaml or fall back to 12.0R."""
    # Baseline schema: spec.risk.per_session.max_loss_usd is the relevant cap.
    # Convert to R: max_loss_usd / risk_dollars_per_trade.
    if spec is None or cfg is None:
        return 12.0
    risk_block = getattr(spec, "risk", None)
    if risk_block is None:
        return 12.0
    per_session = getattr(risk_block, "per_session", None)
    if per_session is None:
        return 12.0
    max_loss = getattr(per_session, "max_loss_usd", None)
    if max_loss is None:
        return 12.0
    risk_ticks = float(getattr(cfg, "risk_ticks", 0)) if cfg is not None else 0.0
    risk_dollars = (
        risk_ticks * float(MNQ_TICK_SIZE) * float(MNQ_POINT_VALUE)
    ) if risk_ticks > 0 else 0.0
    if risk_dollars > 0:
        return float(max_loss) / risk_dollars
    return 12.0


def build_spec_payload(variant_name: str) -> dict[str, Any]:
    """Assemble a Firm-shaped spec_payload for ``variant_name``.

    The dict mirrors what ``firm_live_review.py::_derive_spec_payload``
    produces (sample_size, expected_expectancy_r, oos_degradation_pct,
    entry/stop/target_logic, dd_kill_switch_r, regimes_approved,
    approved_sessions) but is light: no backtest is run.

    A ``provenance`` field tags the source so downstream consumers
    (PM agent, journal) can see whether they're looking at calibrated
    or stub values.
    """
    cfg = _load_variant_config(variant_name)
    spec = _load_baseline_spec()
    daily = _load_cached_backtest(variant_name)

    sample_size, expectancy_r, oos_deg = _derive_sample_stats(cfg, daily)
    provenance: list[str] = []
    if cfg is not None:
        provenance.append("variant_cfg")
    if spec is not None:
        provenance.append("baseline_yaml")
    if daily is not None:
        provenance.append("cached_backtest")
    if not provenance:
        provenance = ["stub"]

    return {
        "strategy_id": variant_name,
        "sample_size": sample_size,
        "expected_expectancy_r": expectancy_r,
        "oos_degradation_pct": oos_deg,
        "entry_logic": _entry_logic_str(cfg),
        "stop_logic": _stop_logic_str(cfg),
        "target_logic": _target_logic_str(cfg),
        "dd_kill_switch_r": _dd_kill_switch_r(cfg, spec),
        "regimes_approved": _approved_regimes(daily),
        "approved_sessions": _approved_sessions(spec),
        "provenance": provenance,
    }
