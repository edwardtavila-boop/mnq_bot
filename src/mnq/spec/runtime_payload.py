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

# Default trades-per-day proxy (v0.2.10 fallback). Used when the
# live_sim journal isn't available or has zero fills. The real value
# is derived per-variant from the journal via ``_journal_trades_per_day``.
TRADES_PER_DAY_PROXY = 2

# Path to the live_sim journal that records every fill. Loaded
# lazily so this module doesn't fail to import when the journal
# infrastructure isn't on the path.


def _journal_trades_per_day() -> float | None:
    """Derive trades-per-day from the live_sim journal.

    Counts FILL_REALIZED events grouped by UTC date; returns
    n_fills / n_distinct_dates. Returns None if the journal is
    missing, empty, or unreadable -- caller falls back to
    TRADES_PER_DAY_PROXY.

    This replaces the v0.2.7 hardcoded ``TRADES_PER_DAY_PROXY = 2``
    with a real-data-derived calibration so each variant's
    sample_size in the Firm review reflects its actual trade rate.
    """
    try:
        from datetime import UTC, datetime

        from mnq.core.paths import LIVE_SIM_JOURNAL
        from mnq.storage.journal import EventJournal
        from mnq.storage.schema import FILL_REALIZED
    except ImportError:
        return None
    if not LIVE_SIM_JOURNAL.exists():
        return None
    try:
        j = EventJournal(LIVE_SIM_JOURNAL)
        dates: set[str] = set()
        n_fills = 0
        for event in j.replay(event_types=(FILL_REALIZED,)):
            n_fills += 1
            ts = getattr(event, "ts", None) or getattr(event, "timestamp", None)
            if ts is None:
                continue
            if isinstance(ts, datetime):
                dates.add(ts.astimezone(UTC).date().isoformat())
            elif isinstance(ts, str):
                # ISO-8601 string -- take the date prefix
                dates.add(ts[:10])
            elif isinstance(ts, (int, float)):
                # Epoch seconds
                dates.add(
                    datetime.fromtimestamp(float(ts), tz=UTC).date().isoformat(),
                )
    except Exception:  # noqa: BLE001 -- defensive; never crash the runtime
        return None
    if n_fills == 0 or not dates:
        return None
    return n_fills / len(dates)


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

    v0.2.10: trades-per-day is now derived from the journal
    (``_journal_trades_per_day``) instead of the hardcoded
    ``TRADES_PER_DAY_PROXY``. Fallback to the constant when the
    journal is missing or empty.
    """
    if not daily_pnl:
        return 0, 0.0, 100.0
    n_days = len(daily_pnl)
    journal_rate = _journal_trades_per_day()
    trades_per_day = journal_rate if journal_rate is not None else TRADES_PER_DAY_PROXY
    sample_size = max(int(round(n_days * trades_per_day)), 1)
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


# Module-level cache for the (expensive) per-day regime classification.
# Each Python process pays the tape-load + classification cost ONCE
# (~30-60s for the full 7-year MNQ 5m tape) and reuses the result for
# every subsequent build_spec_payload call.
_CLASSIFY_CACHE: dict[str, dict[str, str]] = {}

# Disk-backed cache (v0.2.13): the classification map is persisted to
# ``data/cache/regime_per_day.json`` so subsequent Python invocations
# don't pay the 30s warm-up. The cache is keyed by the tape file's
# (size, mtime) -- if either changes the cache is rebuilt. This makes
# the cache safe across tape updates (databento refreshes) without
# requiring a manual invalidation step.


def _disk_cache_path() -> Path:
    """Resolve the disk cache file location."""
    return REPO_ROOT / "data" / "cache" / "regime_per_day.json"


def _tape_signature() -> tuple[int, int] | None:
    """``(size_bytes, mtime_ns)`` of the canonical tape, or None on missing."""
    try:
        from mnq.tape.databento_tape import DEFAULT_DATABENTO_5M
    except ImportError:
        return None
    if not DEFAULT_DATABENTO_5M.exists():
        return None
    st = DEFAULT_DATABENTO_5M.stat()
    return (st.st_size, st.st_mtime_ns)


def _try_load_disk_cache() -> dict[str, str] | None:
    """Read the disk cache. Returns None on missing / stale / unreadable.

    A cache is "stale" if the tape's (size, mtime) has changed since
    the cache was written. Stale caches are silently ignored (the
    in-memory cache will be rebuilt and re-persisted).
    """
    cache_path = _disk_cache_path()
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    sig = _tape_signature()
    cached_sig = data.get("tape_signature")
    if sig is None or cached_sig is None:
        return None
    if list(cached_sig) != list(sig):
        return None
    per_day = data.get("per_day")
    if not isinstance(per_day, dict):
        return None
    # Coerce values to str (they came from json -- safe but explicit)
    return {str(k): str(v) for k, v in per_day.items()}


def _persist_disk_cache(per_day: dict[str, str]) -> None:
    """Persist the classification map atomically (tmpfile + replace).

    Errors are swallowed: persistence is a perf optimization, not a
    correctness requirement. A failed persist just means the next
    process re-pays the warm-up cost.
    """
    cache_path = _disk_cache_path()
    sig = _tape_signature()
    if sig is None:
        return
    payload = {
        "tape_signature": list(sig),
        "n_days": len(per_day),
        "per_day": per_day,
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(cache_path)
    except OSError:
        pass


def _per_day_regime_map() -> dict[str, str] | None:
    """Lazily-cached per-day regime map for the canonical 5m tape.

    Resolution order:
      1. In-memory cache for the current process (fastest)
      2. Disk cache (validated against tape signature)
      3. Re-classify the full tape and persist to disk

    Returns ``{date_iso: regime_label}`` or None on tape load failure.
    """
    cache_key = "default"
    if cache_key in _CLASSIFY_CACHE:
        return _CLASSIFY_CACHE[cache_key]
    # Try disk cache first (cheap)
    disk = _try_load_disk_cache()
    if disk is not None:
        _CLASSIFY_CACHE[cache_key] = disk
        return disk
    # Cold path: re-classify and persist
    try:
        from mnq.regime import classify_per_day
        from mnq.tape import iter_databento_bars
    except ImportError:
        return None
    try:
        all_bars: list[Any] = list(iter_databento_bars())
    except (FileNotFoundError, OSError):
        return None
    if not all_bars:
        return None
    per_day = {k: v.value for k, v in classify_per_day(all_bars).items()}
    _CLASSIFY_CACHE[cache_key] = per_day
    _persist_disk_cache(per_day)
    return per_day


def _approved_regimes_from_tape(
    daily_pnl: dict[str, float],
) -> list[str] | None:
    """Use the real-tape per-day regime classifier to derive
    ``regimes_approved`` from positive-PnL days.

    Returns ``None`` if the tape isn't available or the classifier
    can't be loaded -- caller falls back to the legacy stub. Returns
    a sorted list of unique regime labels otherwise.

    v0.2.12: replaces the v0.2.7 stub ("any positive-PnL day ->
    normal_vol_trend") with a real per-day classification. The Firm
    MacroAgent uses ``regimes_approved`` to decide whether the
    strategy's wins came in conditions that *could happen again*; a
    real classification beats the stub for that decision.
    """
    pos_days = {d for d, v in daily_pnl.items() if v > 0}
    if not pos_days:
        return []
    per_day = _per_day_regime_map()
    if per_day is None:
        return None
    regimes = {
        per_day[day]
        for day in pos_days
        if day in per_day
    }
    if not regimes:
        return None
    return sorted(regimes)


def _approved_regimes(daily_pnl: dict[str, float] | None) -> list[str]:
    """Approved regimes: prefer real classification, fall back to stub.

    v0.2.12: tries the real per-day classifier first. Falls back to
    the v0.2.7 coarse stub ("any positive-PnL day -> normal_vol_trend")
    when the tape or classifier is unavailable so existing variants
    don't regress.
    """
    if not daily_pnl:
        return []
    real = _approved_regimes_from_tape(daily_pnl)
    if real is not None:
        return real
    pos_days = [d for d, v in daily_pnl.items() if v > 0]
    if pos_days:
        return ["normal_vol_trend"]
    return []


def _regime_expectancy_stats(
    cfg: Any,
    daily_pnl: dict[str, float] | None,
) -> dict[str, dict[str, float]]:
    """Per-regime aggregate stats for the variant's day-by-day results.

    For each regime where the variant has at least one trading day,
    returns:
      n_days       -- count of days the variant traded in this regime
      total_pnl    -- sum of P&L (in dollars) across those days
      pnl_per_day  -- mean P&L per day
      expectancy_r -- pnl_per_day / risk_dollars / trades_per_day
                      (per-trade R, same units as the top-level
                      ``expected_expectancy_r``)

    The Firm MacroAgent reads this dict to answer "does the variant
    actually have edge in regime X, or is regime X just rare in the
    sample?" If a regime shows up in ``regimes_approved`` but its
    n_days is 1 of 15, the answer is "regime is rare, evidence is
    thin" -- a verdict the agent can't reach from
    ``regimes_approved`` alone.

    Returns ``{}`` (empty dict) when daily_pnl is None or the
    classifier is unavailable. Empty maps to "no per-regime evidence"
    in the Firm payload, distinct from a regime with n_days=0 (which
    means "we know the regime exists but the strategy never traded it").
    """
    if not daily_pnl:
        return {}
    per_day = _per_day_regime_map()
    if per_day is None:
        return {}
    # Group dates by classified regime
    by_regime: dict[str, list[float]] = {}
    for date, pnl in daily_pnl.items():
        regime = per_day.get(date)
        if regime is None:
            continue
        by_regime.setdefault(regime, []).append(pnl)
    if not by_regime:
        return {}
    # Compute stats
    risk_ticks = float(getattr(cfg, "risk_ticks", 0)) if cfg is not None else 0.0
    risk_dollars = (
        risk_ticks * float(MNQ_TICK_SIZE) * float(MNQ_POINT_VALUE)
    ) if risk_ticks > 0 else 0.0
    rate = _journal_trades_per_day() or float(TRADES_PER_DAY_PROXY)
    out: dict[str, dict[str, float]] = {}
    for regime, pnls in by_regime.items():
        n = len(pnls)
        total = sum(pnls)
        pnl_per_day = total / n if n else 0.0
        # expectancy_r per trade: pnl_per_day / (rate * risk_dollars)
        expectancy_r = (
            pnl_per_day / (rate * risk_dollars)
            if risk_dollars > 0 and rate > 0 else 0.0
        )
        out[regime] = {
            "n_days": float(n),
            "total_pnl": float(total),
            "pnl_per_day": float(pnl_per_day),
            "expectancy_r": float(expectancy_r),
        }
    return out


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
        # v0.2.13: per-regime expectancy + n_days. Lets the Firm
        # MacroAgent see "regime X passed BUT n_days=1 of 15" -- a
        # verdict the agent can't reach from regimes_approved alone.
        "regime_expectancy": _regime_expectancy_stats(cfg, daily),
        "approved_sessions": _approved_sessions(spec),
        "provenance": provenance,
    }
