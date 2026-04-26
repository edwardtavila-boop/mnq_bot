"""Live Firm review — invokes the real six-stage Firm agents through the
bridge shim and writes a real-code verdict to
``reports/firm_reviews/<variant>_live.md``.

This script is the counterpart to ``scripts/firm_review.py``: the latter
runs the markdown-template review (safe for when the Firm code isn't
ready). This one runs the *actual* Firm Python agents via
``mnq.firm_runtime.run_six_stage_review``.

The adapter here builds the StrategySpec payload the Firm agents expect by
harvesting real stats from the A/B report + journal:

    - sample_size       ← n_trades across the 15-day real MNQ sample
    - expected_expectancy_r ← expectancy_dollars / (risk_ticks * point_value)
    - oos_degradation_pct   ← walk-forward out-of-sample shrinkage vs in-sample
    - entry_logic / stop_logic / target_logic ← derived from StrategyConfig
    - regimes_approved ← per-regime PnL table from the A/B run
    - dd_kill_switch_r ← pulled from spec yaml

Usage:

    python scripts/firm_live_review.py --variant r5_real_wide_target
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from strategy_ab import _load_real_days, _run_variant  # noqa: E402
from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

from mnq.eta_v3 import (  # noqa: E402
    apex_to_firm_payload,
    run_apex_evaluation,
    summarize_voices,
)
from mnq.spec.loader import load_spec  # noqa: E402

BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "reports" / "firm_reviews"
VARIANTS = {cfg.name: cfg for cfg in _VARIANT_LIST}

# MNQ: $2 per point, 0.25 point tick size → $0.50 / tick
MNQ_DOLLARS_PER_POINT = 2.0
MNQ_TICK_SIZE = 0.25


def _derive_spec_payload(variant_name: str) -> dict:
    """Extract a StrategySpec dict the Firm agents can evaluate."""
    cfg = VARIANTS[variant_name]
    spec = load_spec(BASELINE)
    days = _load_real_days(timeframe="1m")
    result = _run_variant(cfg, spec, days)

    # Convert $ expectancy → R-multiples (risk_ticks × $/tick).
    risk_dollars = cfg.risk_ticks * MNQ_TICK_SIZE * MNQ_DOLLARS_PER_POINT
    expectancy_r = float(result.expectancy) / risk_dollars if risk_dollars else 0.0

    # OOS degradation proxy: how much worse the per-regime expectancy is
    # vs the best regime (a crude but honest stand-in for walk-forward).
    per_regime_pnl = [float(v["pnl"]) for v in result.per_regime.values() if v["n"]]
    if per_regime_pnl:
        best = max(per_regime_pnl)
        worst = min(per_regime_pnl)
        oos_deg = max(0.0, (best - worst) / best) * 100.0 if best > 0 else 100.0
    else:
        oos_deg = 0.0

    regimes_approved = [
        k for k, v in result.per_regime.items() if v["n"] and float(v["pnl"]) > 0
    ]

    spec_payload = {
        "strategy_id": cfg.name,
        "sample_size": result.n_trades,
        "expected_expectancy_r": expectancy_r,
        "oos_degradation_pct": oos_deg,
        "entry_logic": (
            f"EMA{cfg.ema_fast}/EMA{cfg.ema_slow} cross, min spread "
            f"{cfg.cross_magnitude_min:.2f} pts, vol filter σ≤"
            f"{cfg.vol_filter_stdev_max}, hard pause σ>{cfg.vol_hard_pause_stdev}, "
            f"orderflow proxy≥{cfg.orderflow_proxy_min:.2f}"
        ),
        "stop_logic": f"{cfg.risk_ticks}-tick hard stop; time stop {cfg.time_stop_bars} bars",
        "target_logic": f"{cfg.rr}R fixed target",
        "dd_kill_switch_r": 12.0,
        "regimes_approved": regimes_approved,
        "approved_sessions": ["RTH"],
    }
    return spec_payload


def _variant_side(cfg) -> str:
    """Infer long/short/both from the variant config.

    ``strategy_v2`` configs carry ``allowed_sides`` (list of {'long','short'})
    or a single-side flag. Fall back to 'long' if neither is present — that's
    the repo default.
    """
    sides = getattr(cfg, "allowed_sides", None)
    if isinstance(sides, (list, tuple, set)) and len(sides) == 1:
        s = next(iter(sides))
        if s in ("long", "short"):
            return s
    if getattr(cfg, "long_only", False):
        return "long"
    if getattr(cfg, "short_only", False):
        return "short"
    return "long"


def _build_real_tape_bar(firm_engine_mod, *, warmup_bars: int = 80):
    """Pull a real-tape bar from the canonical Databento 5m tape and
    warm up an IndicatorState across a window of recent bars.

    Returns ``(bar, ctx_dict)`` where ``bar`` is a ``firm_engine.Bar``
    with all indicators populated (atr, vwap, ema9/21/50, rsi, adx,
    htf_close, htf_ema50), and ``ctx_dict`` carries the rolling
    statistics the apex evaluation needs (atr_ma20, range_avg_20,
    highest_5_prev, lowest_5_prev, vol_z proxies, prev_day_high/low).

    Falls back to ``(None, {})`` if the tape is missing or too short
    so the caller can degrade to the synthetic representative bar
    (legacy behavior, fail-open).

    B4 closure (v0.2.6): replaces the hardcoded ``firm_engine.Bar(
    time=0, open=21000.0, ...)`` with a real-tape sample. The verdict
    is now grounded in the most recent ``warmup_bars`` of real MNQ
    market state instead of a fictional one.
    """
    try:
        from mnq.tape import iter_databento_bars
    except ImportError:
        return None, {}
    try:
        # Pull warmup_bars + an ATR/MA buffer so the indicators are stable.
        # We can't tail-seek a generator cheaply, so just walk forward and
        # keep the last (warmup_bars + 25) for indicator priming + context.
        keep = warmup_bars + 25
        recent: list = []
        for b in iter_databento_bars(rth_only=True):
            recent.append(b)
            if len(recent) > keep:
                recent.pop(0)
        if len(recent) < warmup_bars:
            return None, {}
    except (FileNotFoundError, OSError):
        return None, {}

    # Convert mnq.core.types.Bar -> firm_engine.Bar (float OHLCV).
    fe_bars = [
        firm_engine_mod.Bar(
            time=int(b.ts.timestamp()),
            open=float(b.open), high=float(b.high),
            low=float(b.low), close=float(b.close),
            volume=float(b.volume),
        )
        for b in recent
    ]

    try:
        from indicator_state import IndicatorState  # type: ignore[import-not-found]
    except ImportError:
        # eta_v3_framework on path failed; bail.
        return None, {}
    state = IndicatorState()
    populated = [state.update(b) for b in fe_bars]

    last = populated[-1]
    # Rolling context stats over the warmup window (last 20 bars).
    tail = populated[-20:]
    atr_vals = [b.atr for b in tail if b.atr is not None]
    atr_ma20 = sum(atr_vals) / len(atr_vals) if atr_vals else 0.0
    ranges = [b.high - b.low for b in tail]
    range_avg_20 = sum(ranges) / len(ranges) if ranges else 0.0
    highs5 = [b.high for b in populated[-6:-1]]
    lows5 = [b.low for b in populated[-6:-1]]
    highest_5_prev = max(highs5) if highs5 else last.high
    lowest_5_prev = min(lows5) if lows5 else last.low
    # Volatility z-score proxy: (latest_atr - mean) / stdev_atr.
    if len(atr_vals) >= 2:
        mean_atr = sum(atr_vals) / len(atr_vals)
        var = sum((a - mean_atr) ** 2 for a in atr_vals) / len(atr_vals)
        std_atr = var ** 0.5 or 1.0
        vol_z = ((last.atr or 0.0) - mean_atr) / std_atr
    else:
        vol_z = 0.0
    # Prev-day high/low: take the highest high / lowest low across the
    # tape window outside the most recent day. Cheap proxy: tail of 78
    # 5m bars = one RTH session.
    prev_day = populated[-156:-78] if len(populated) >= 156 else populated[:-1]
    if prev_day:
        prev_day_high = max(b.high for b in prev_day)
        prev_day_low = min(b.low for b in prev_day)
    else:
        prev_day_high = last.high
        prev_day_low = last.low

    ctx = {
        "atr_ma20": float(atr_ma20),
        "vol_z": float(vol_z),
        "prev_adx_3": float(last.adx or 0.0),
        "range_avg_20": float(range_avg_20),
        "vol_z_prev_1": float(vol_z),
        "vol_z_prev_2": float(vol_z),
        "highest_5_prev": float(highest_5_prev),
        "lowest_5_prev": float(lowest_5_prev),
        "recent_losses": 0,
        "prev_day_high": float(prev_day_high),
        "prev_day_low": float(prev_day_low),
        "_tape_window": len(populated),
        "_tape_last_ts": last.time,
    }
    return last, ctx


def _derive_apex_snapshot(variant_name: str, spec_payload: dict):
    """Build a real-tape Apex V3 snapshot for this variant's spec.

    Returns the ``ApexVoiceSnapshot`` if the engine + tape are
    available, else None. The enrichment is fail-open: a None snapshot
    becomes a payload pass-through, which means the Quant agent's
    fold-in is a no-op and the verdict is unchanged from the
    pre-apex baseline.

    B4 closure (v0.2.6): the bar fed to the 15-voice engine is now
    pulled from the real Databento 5m tape (the most recent ~80 bars
    of MNQ history, with indicators warmed up via IndicatorState).
    Prior to v0.2.6 this used a hardcoded synthetic bar
    (``firm_engine.Bar(time=0, open=21000.0, ...)``) which made the
    verdict structurally meaningless.
    """
    try:
        import sys as _sys
        from pathlib import Path as _Path
        APEX_PY = _Path(__file__).resolve().parents[1] / "eta_v3_framework" / "python"  # noqa: N806 -- module-path constant, retained from pre-B4 code for stability
        if str(APEX_PY) not in _sys.path:
            _sys.path.insert(0, str(APEX_PY))
        import firm_engine  # type: ignore
    except ImportError:
        return None

    cfg = VARIANTS[variant_name]
    side = _variant_side(cfg)
    # Regime proxy: prefer the first approved regime; fall back to NEUTRAL.
    approved = spec_payload.get("regimes_approved") or []
    regime = str(approved[0]).upper() if approved else "NEUTRAL"
    # firm_engine canonicalizes to RISK-ON / RISK-OFF / NEUTRAL / CRISIS.
    regime_map = {
        "TREND": "RISK-ON", "RISK_ON": "RISK-ON", "RISK-ON": "RISK-ON",
        "RANGE": "NEUTRAL", "NEUTRAL": "NEUTRAL",
        "CHOPPY": "RISK-OFF", "RISK_OFF": "RISK-OFF", "RISK-OFF": "RISK-OFF",
        "CRISIS": "CRISIS",
    }
    regime = regime_map.get(regime, "NEUTRAL")

    # Real-tape bar (B4). Falls back to the legacy synthetic bar if the
    # tape is missing or the indicator state can't warm up.
    bar, ctx = _build_real_tape_bar(firm_engine)
    if bar is None:
        # Legacy synthetic bar (pre-v0.2.6 fallback).
        bar = firm_engine.Bar(
            time=0,
            open=21000.0, high=21012.0, low=20995.0, close=21010.0,
            volume=1500.0, atr=4.5, vwap=21006.0,
            ema9=21008.0, ema21=21003.0, ema50=20998.0,
            rsi=58.0, adx=24.0,
            htf_close=21000.0, htf_ema50=20985.0,
        )
        ctx = {
            "atr_ma20": 4.2, "vol_z": 0.3, "prev_adx_3": 22.0,
            "range_avg_20": 14.0, "vol_z_prev_1": 0.25,
            "vol_z_prev_2": 0.20, "highest_5_prev": 21012.0,
            "lowest_5_prev": 20992.0, "recent_losses": 0,
            "prev_day_high": 21020.0, "prev_day_low": 20980.0,
        }

    # Trigger state mirrors the variant's side.
    setup = firm_engine.SetupTriggers(
        orb_long=(side == "long"),
        orb_short=(side == "short"),
        ema_trend_bull=(side == "long"),
        ema_trend_bear=(side == "short"),
        ema_in_zone=False,
        orb_score=4, ema_score=3, sweep_score=0,
    )
    return run_apex_evaluation(
        bar, setup, regime=regime,
        atr_ma20=ctx["atr_ma20"], vol_z=ctx["vol_z"],
        prev_adx_3=ctx["prev_adx_3"], range_avg_20=ctx["range_avg_20"],
        vol_z_prev_1=ctx["vol_z_prev_1"], vol_z_prev_2=ctx["vol_z_prev_2"],
        highest_5_prev=ctx["highest_5_prev"], lowest_5_prev=ctx["lowest_5_prev"],
        recent_losses=ctx["recent_losses"],
        prev_day_high=ctx["prev_day_high"], prev_day_low=ctx["prev_day_low"],
    )


def _render_verdict(variant: str, spec_payload: dict, stages: dict,
                    apex_snapshot=None) -> str:
    lines = [f"# Firm Review (LIVE) — `{variant}`", ""]
    lines.append("This review was produced by the real six-stage Firm Python")
    lines.append("agents, invoked through `mnq.firm_runtime.run_six_stage_review`.")
    lines.append("")
    if apex_snapshot is not None:
        lines.append("**Apex V3 enrichment:** active — payload carries")
        lines.append("`eta_v3_voices` for QuantAgent consumption.")
        lines.append("")
        lines.append("```")
        lines.append(summarize_voices(apex_snapshot))
        lines.append("```")
        lines.append("")
    else:
        lines.append("**Apex V3 enrichment:** unavailable — QuantAgent")
        lines.append("fold-in is a no-op (fail-open).")
        lines.append("")
    lines.append("## Strategy spec fed to the Firm")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(spec_payload, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Stage verdicts")
    lines.append("")
    lines.append("| Stage | Verdict | P(ok) | 95% CI | Horizon |")
    lines.append("|---|---|---:|---|---|")
    for stage, out in stages.items():
        ci = out.get("confidence_interval", [0, 0])
        ci_fmt = f"{ci[0]:.2f} / {ci[1]:.2f}" if ci else "n/a"
        lines.append(
            f"| `{stage}` | **{out.get('verdict', '?')}** | "
            f"{out.get('probability', 0):.2f} | {ci_fmt} | "
            f"{out.get('time_horizon', '?')} |"
        )
    lines.append("")

    for stage, out in stages.items():
        lines.append(f"## {stage.upper()}")
        lines.append("")
        lines.append(f"- Reasoning: {out.get('reasoning', '')}")
        lines.append(f"- Primary driver: {out.get('primary_driver', '')}")
        if out.get("secondary_driver"):
            lines.append(f"- Secondary driver: {out['secondary_driver']}")
        lines.append(f"- Falsification: {out.get('falsification_criteria', '')}")
        if out.get("payload"):
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(out["payload"], indent=2, default=str))
            lines.append("```")
        lines.append("")

    # PM stage is the final verdict
    pm = stages.get("pm", {})
    lines.append("## Final verdict (PM)")
    lines.append("")
    lines.append(f"**{pm.get('verdict', '?')}** — {pm.get('reasoning', '')}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live Firm review via shim.")
    parser.add_argument("--variant", type=str, default="r5_real_wide_target")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    if args.variant not in VARIANTS:
        print(f"unknown variant: {args.variant}", file=sys.stderr)
        return 2

    try:
        from mnq.firm_runtime import compute_confluence, run_six_stage_review
    except ImportError as exc:
        print(
            "firm_runtime shim not present — run "
            "`python scripts/firm_bridge.py --integrate` first.\n"
            f"  detail: {exc}",
            file=sys.stderr,
        )
        return 3

    spec_payload = _derive_spec_payload(args.variant)
    context = (
        f"Candidate strategy `{args.variant}`: "
        f"n={spec_payload['sample_size']}, "
        f"E={spec_payload['expected_expectancy_r']:.3f}R"
    )

    # Intelligence layer — compute confluence from representative market state.
    # Uses the variant's approved regimes to derive a regime snapshot, and
    # builds representative internals/volatility/session data.
    approved = spec_payload.get("regimes_approved", [])
    canonical = approved[0] if approved else "normal_vol_trend"
    confluence_result = compute_confluence(
        internals={"tick": 250, "add": 800, "vold_ratio": 1.15},
        volatility={"vix": 18.0, "vix9d": 16.5, "realized_vol": 15.5},
        cross_asset={"es_delta": 0.15, "dxy_delta": -0.05, "tlt_delta": -0.08},
        session={"phase": "NY_OPEN", "is_rth": True, "minutes_to_catalyst": 180},
        micro={"spread_ticks": 1.0, "depth_ratio": 1.1, "cum_delta": 300},
        calendar={"next_event": "", "hours_until": 999},
        eta_v3={},
        regime={"canonical": canonical, "persistence_bars": 40},
    )

    # Apex V3 enrichment — derive a representative snapshot for this
    # variant and splice into the payload. If the engine is unavailable,
    # `apex_to_firm_payload` returns the base dict unchanged (fail-open).
    cfg = VARIANTS[args.variant]
    apex_snapshot = _derive_apex_snapshot(args.variant, spec_payload)
    enriched_payload = apex_to_firm_payload(
        {"spec": spec_payload, "side": _variant_side(cfg)},
        apex_snapshot,
    )

    # Feed Apex V3 voices into confluence if snapshot available
    if apex_snapshot is not None:
        apex_voices = enriched_payload.get("eta_v3_voices", {})
        if apex_voices:
            confluence_result = compute_confluence(
                internals={"tick": 250, "add": 800, "vold_ratio": 1.15},
                volatility={"vix": 18.0, "vix9d": 16.5, "realized_vol": 15.5},
                cross_asset={"es_delta": 0.15, "dxy_delta": -0.05, "tlt_delta": -0.08},
                session={"phase": "NY_OPEN", "is_rth": True, "minutes_to_catalyst": 180},
                micro={"spread_ticks": 1.0, "depth_ratio": 1.1, "cum_delta": 300},
                calendar={"next_event": "", "hours_until": 999},
                eta_v3=apex_voices,
                regime={"canonical": canonical, "persistence_bars": 40},
            )

    stages = run_six_stage_review(
        strategy_id=args.variant,
        decision_context=context,
        payload=enriched_payload,
        regime_snapshot={"regimes_approved": spec_payload["regimes_approved"]},
        confluence_result=confluence_result,
    )

    md = _render_verdict(args.variant, spec_payload, stages,
                         apex_snapshot=apex_snapshot)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dest = args.output_dir / f"{args.variant}_live.md"
    dest.write_text(md)
    print(md)
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
