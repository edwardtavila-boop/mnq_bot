"""
Apex v2 Autopilot
=================
Single entrypoint. Runs the full end-to-end loop with The Firm making every
system-level decision. No flags required.

Usage:
    python autopilot.py                   # default: backtest + auto-tune + report
    python autopilot.py --mode live       # connect to webhook for live
    python autopilot.py --mode calibrate  # run calibration only
    python autopilot.py --data custom.csv # override default data file

The autopilot:
  1. Auto-discovers data (MNQ 5m + intermarket feeds)
  2. Loads prior winning configs (if any)
  3. Runs a short recent window to build meta-context
  4. Meta-Firm votes on: regime, PM, setups, risk budget
  5. Runs backtest with Meta-Firm's chosen config
  6. Validates against health checks
  7. Writes session report + saves new config if it won
  8. Prints terse one-screen summary
"""

import argparse
import json
import os
import sys
import glob
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List

from firm_engine import FirmConfig
from backtest import Backtester, V1DetectorConfig, load_csv
from intermarket import load_with_intermarket, coverage_report
from firm_meta import MetaContext, run_meta_firm, save_meta_decision, load_recent_meta
from autocalibrator import run_test, score as calib_score

ET = ZoneInfo("America/New_York")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")
STATE_DIR = os.path.join(BASE_DIR, "state")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(CONFIGS_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# DATA AUTO-DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────
def auto_discover_data():
    """Find MNQ + intermarket CSVs in known locations."""
    candidates = [
        "/tmp/mnq_5m_real.csv",
        "/tmp/nq_data/mnq_5m.csv",
        os.path.join(BASE_DIR, "../data/mnq_5m.csv"),
    ]
    mnq = next((c for c in candidates if os.path.exists(c)), None)
    if not mnq:
        return {"mnq": None}

    im_dir = os.path.dirname(mnq)
    if "nq_data" not in im_dir:
        im_dir = "/tmp/nq_data"

    return {
        "mnq": mnq,
        "vix":  os.path.join(im_dir, "mnq_vix_5.csv")   if os.path.exists(os.path.join(im_dir, "mnq_vix_5.csv"))   else None,
        "es":   os.path.join(im_dir, "mnq_es1_5.csv")   if os.path.exists(os.path.join(im_dir, "mnq_es1_5.csv"))   else None,
        "dxy":  os.path.join(im_dir, "mnq_dxy_5.csv")   if os.path.exists(os.path.join(im_dir, "mnq_dxy_5.csv"))   else None,
        "tick": os.path.join(im_dir, "mnq_tick_5.csv")  if os.path.exists(os.path.join(im_dir, "mnq_tick_5.csv"))  else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# META-CONTEXT BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def build_meta_context(bars, recent_trades_snapshot: list) -> MetaContext:
    """Build a MetaContext from current state and recent backtest results."""
    ctx = MetaContext()
    ctx.now_utc = datetime.now(timezone.utc)
    et_now = ctx.now_utc.astimezone(ET)
    ctx.hour_et = et_now.hour
    ctx.weekday = et_now.isoweekday()

    # Recent trades (last 20)
    ctx.recent_trades = recent_trades_snapshot[-20:]

    if ctx.recent_trades:
        wins = sum(1 for t in ctx.recent_trades if t.get("pnl_r", 0) > 0)
        losses = sum(1 for t in ctx.recent_trades if t.get("pnl_r", 0) < 0)
        ctx.rolling_win_rate = wins / len(ctx.recent_trades)
        gw = sum(t.get("pnl_r", 0) for t in ctx.recent_trades if t.get("pnl_r", 0) > 0)
        gl = abs(sum(t.get("pnl_r", 0) for t in ctx.recent_trades if t.get("pnl_r", 0) < 0))
        ctx.rolling_pf = gw / gl if gl > 0 else 999.0
        cum = 0; peak = 0; dd = 0
        for t in ctx.recent_trades:
            cum += t.get("pnl_r", 0)
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        ctx.rolling_dd = dd
        ctx.current_equity_r = cum
        ctx.peak_equity_r = peak

        # Streaks
        streak_l = 0; streak_w = 0
        for t in reversed(ctx.recent_trades):
            r = t.get("pnl_r", 0)
            if r < 0 and streak_w == 0:
                streak_l += 1
            elif r > 0 and streak_l == 0:
                streak_w += 1
            else:
                break
        ctx.consecutive_losses = streak_l
        ctx.consecutive_wins = streak_w

    # Last ~20 bars: ATR, ADX, vol_z averages
    tail = bars[-50:] if len(bars) > 50 else bars
    atrs = [b.atr for b in tail if b.atr is not None]
    adxs = [b.adx for b in tail if b.adx is not None]
    if atrs: ctx.avg_atr = sum(atrs) / len(atrs)
    if adxs: ctx.avg_adx = sum(adxs) / len(adxs)

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# SELF-TUNING CALIBRATION
# ─────────────────────────────────────────────────────────────────────────────
def self_calibrate(bars, n_windows: int = 7, verbose: bool = False):
    """Firm-guided calibration: try configs, Firm picks by weighted score."""
    pm_grid = [25, 30, 35, 40]
    rw_grid = [0.7, 1.0, 1.3]
    orb_to_grid = [15, 20]
    ema_s_grid = [4]

    if verbose:
        print(f"  Calibration grid: {len(pm_grid)*len(rw_grid)*len(orb_to_grid)*len(ema_s_grid)} configs")

    results = []
    for pm in pm_grid:
        for rw in rw_grid:
            for orb_to in orb_to_grid:
                for ema_s in ema_s_grid:
                    cfg = FirmConfig(pm_threshold=pm, redteam_weight=rw, require_setup=True)
                    det_cfg = V1DetectorConfig(orb_timeout=orb_to, ema_min_score=ema_s)
                    stats = run_test(bars, cfg, det_cfg, n_windows)
                    sc = calib_score(stats)
                    results.append({
                        "pm": pm, "red_w": rw, "orb_to": orb_to, "ema_s": ema_s,
                        "score": round(sc, 2), **stats,
                    })

    results.sort(key=lambda x: -x["score"])
    return results


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECKS
# ─────────────────────────────────────────────────────────────────────────────
def health_check(bars, summary: dict) -> List[dict]:
    """Return list of issues found. Empty list = all good."""
    issues = []

    # Data staleness: last bar should be recent-ish
    if bars:
        last_ts = bars[-1].time
        last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
        age_days = (datetime.now(timezone.utc) - last_dt).days
        if age_days > 30:
            issues.append({"severity": "warning", "msg": f"Data is {age_days}d old"})
        if age_days > 90:
            issues.append({"severity": "critical", "msg": f"Data >90d old — verify source"})

    # Trade count sanity
    if summary.get("trades", 0) == 0:
        issues.append({"severity": "warning", "msg": "No trades fired — check PM/filters"})
    elif summary.get("trades", 0) > 200:
        issues.append({"severity": "warning", "msg": f"Very high trade count ({summary['trades']}) — likely over-trading"})

    # Drawdown check
    if summary.get("max_drawdown_r", 0) > 4:
        issues.append({"severity": "critical", "msg": f"Max DD {summary['max_drawdown_r']:.1f}R exceeds safety threshold (4R)"})

    # Profit factor sanity
    pf = summary.get("profit_factor", 0)
    if isinstance(pf, (int, float)) and pf < 1.0 and summary.get("trades", 0) > 5:
        issues.append({"severity": "warning", "msg": f"PF<1.0 over {summary['trades']} trades"})

    # Intermarket coverage
    if isinstance(summary.get("im_coverage"), dict):
        cov = summary["im_coverage"]
        vix_pct = cov.get("with_vix", 0) / max(cov.get("total_bars", 1), 1) * 100
        if vix_pct < 20:
            issues.append({"severity": "info", "msg": f"VIX coverage only {vix_pct:.0f}% — V8 voice impact limited"})

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────
def save_winning_config(config: dict, stats: dict):
    """Save a winning config to configs/ with timestamp."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(CONFIGS_DIR, f"winner_{ts}.json")
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "stats": stats,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    # Also save as "latest.json" for easy loading
    with open(os.path.join(CONFIGS_DIR, "latest.json"), "w") as f:
        json.dump(payload, f, indent=2)


def load_latest_config() -> Optional[dict]:
    path = os.path.join(CONFIGS_DIR, "latest.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# REPORT GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def generate_report(bars, data_paths, meta_dec, calib_results, final_stats,
                    health_issues, equity_curve):
    """Write comprehensive JSON + human-readable report."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "mnq_path": data_paths.get("mnq"),
            "bars": len(bars) if bars else 0,
            "start": datetime.fromtimestamp(bars[0].time, tz=timezone.utc).isoformat() if bars else None,
            "end": datetime.fromtimestamp(bars[-1].time, tz=timezone.utc).isoformat() if bars else None,
            "intermarket": {k: bool(data_paths.get(k)) for k in ["vix", "es", "dxy", "tick"]},
        },
        "meta_decision": {
            "regime_vote": meta_dec.regime_vote,
            "pm_threshold": meta_dec.pm_threshold,
            "enabled_setups": meta_dec.enabled_setups,
            "risk_budget_R": meta_dec.risk_budget_R,
            "size_multiplier": meta_dec.size_multiplier,
            "trade_allowed": meta_dec.trade_allowed,
            "confidence": meta_dec.confidence,
            "reason": meta_dec.reason,
            "voices": meta_dec.voices,
            "audit": meta_dec.audit,
        },
        "calibration": {
            "top_5": calib_results[:5],
            "best": calib_results[0] if calib_results else None,
        },
        "backtest_summary": final_stats,
        "health_issues": health_issues,
        "equity_curve_summary": {
            "points": len(equity_curve),
            "peak": max((r for _, r in equity_curve), default=0),
            "final": equity_curve[-1][1] if equity_curve else 0,
        } if equity_curve else {},
    }

    # Write JSON
    json_path = os.path.join(REPORTS_DIR, f"session_{ts}.json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Write latest.json for easy retrieval
    with open(os.path.join(REPORTS_DIR, "latest.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)

    return json_path, report


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────
def run_autopilot(mode: str = "default", data_override: Optional[str] = None,
                  verbose: bool = True):
    """Main entrypoint. Returns report dict."""
    start_time = time.time()

    print("┌─────────────────────────────────────────────────────────────────┐")
    print("│         APEX v2 AUTOPILOT — The Firm decides everything        │")
    print("└─────────────────────────────────────────────────────────────────┘")
    print()

    # 1. Auto-discover data
    print("[1/7] Data discovery...")
    paths = auto_discover_data()
    if data_override:
        paths["mnq"] = data_override
    if not paths["mnq"] or not os.path.exists(paths["mnq"]):
        print(f"  ✗ No data file found. Specify with --data <path>")
        return None
    print(f"  ✓ MNQ: {os.path.basename(paths['mnq'])}")
    im_found = [k for k in ["vix", "es", "dxy", "tick"] if paths.get(k)]
    print(f"  ✓ Intermarket: {', '.join(im_found) if im_found else '(none)'}")

    # 2. Load data
    print("[2/7] Loading bars...")
    if im_found:
        bars = load_with_intermarket(paths["mnq"],
                                     vix=paths.get("vix"), es=paths.get("es"),
                                     dxy=paths.get("dxy"), tick=paths.get("tick"))
        cov = coverage_report(bars)
    else:
        bars = load_csv(paths["mnq"])
        cov = {}
    print(f"  ✓ {len(bars)} bars loaded")

    # 3. Load prior winning config
    print("[3/7] Load prior config...")
    prior = load_latest_config()
    base_pm = prior["config"]["pm_threshold"] if prior else 30.0
    print(f"  ✓ Prior PM: {base_pm}" + (" (from previous winner)" if prior else " (default)"))

    # 4. Initial quick backtest to build meta-context
    print("[4/7] Quick context probe...")
    cfg0 = FirmConfig(pm_threshold=base_pm, require_setup=True)
    det_cfg0 = V1DetectorConfig()
    bt0 = Backtester(cfg=cfg0, detector_cfg=det_cfg0)
    summary0 = bt0.run(bars)
    recent_trades_snapshot = [
        {"pnl_r": t.pnl_r, "setup": t.setup, "regime": t.regime}
        for t in bt0.trades
    ]
    print(f"  ✓ Context: {summary0.get('trades', 0)} trades, {summary0.get('win_rate', 0)}% win, {summary0.get('total_r', 0):+.2f}R")

    # 5. Meta-Firm decides system-level params
    print("[5/7] Meta-Firm voting...")
    ctx = build_meta_context(bars, recent_trades_snapshot)
    meta_dec = run_meta_firm(ctx, base_pm=base_pm)
    save_meta_decision(meta_dec, os.path.join(STATE_DIR, f"meta_{int(time.time())}.json"))
    print(f"  ✓ Confidence: {meta_dec.confidence}/100")
    print(f"  ✓ Regime vote: {meta_dec.regime_vote}")
    print(f"  ✓ PM: {meta_dec.pm_threshold}  Budget: {meta_dec.risk_budget_R}R  Size: {meta_dec.size_multiplier}x")
    print(f"  ✓ Enabled: {', '.join(meta_dec.enabled_setups)}")
    print(f"  ✓ Decision: {meta_dec.reason}")

    # 6. Self-calibration + final backtest
    print("[6/7] Self-calibrating...")
    calib_results = self_calibrate(bars, n_windows=7, verbose=verbose)
    best = calib_results[0]
    print(f"  ✓ Grid tested: {len(calib_results)} configs")
    print(f"  ✓ Best: PM={best['pm']}  RW={best['red_w']}  score={best['score']:+.1f}")

    # Apply meta-decision overrides to best config
    # Precedence logic: calibrator winner wins when:
    #   - meta_confidence >= 30 (not alarm mode)
    #   - calibrator has positive score (a real edge)
    # Otherwise meta's caution takes over
    use_calibrator_pm = (meta_dec.trade_allowed
                        and meta_dec.confidence >= 30
                        and best.get("score", -999) > 10)

    if use_calibrator_pm:
        final_pm = best["pm"]
        pm_source = f"calibrator (score={best['score']:+.1f})"
    elif not meta_dec.trade_allowed:
        final_pm = 100.0  # effectively pause
        pm_source = "PAUSED by meta-Firm"
    else:
        final_pm = meta_dec.pm_threshold
        pm_source = f"meta-Firm (conf={meta_dec.confidence})"

    print(f"  ✓ PM source: {pm_source} → {final_pm}")

    final_cfg = FirmConfig(
        pm_threshold=final_pm,
        redteam_weight=best["red_w"],
        require_setup=True,
    )
    final_det_cfg = V1DetectorConfig(
        orb_timeout=best["orb_to"],
        ema_min_score=best["ema_s"],
    )
    bt_final = Backtester(cfg=final_cfg, detector_cfg=final_det_cfg)
    final_summary = bt_final.run(bars)
    final_summary["im_coverage"] = cov
    print(f"  ✓ Final backtest: {final_summary.get('trades', 0)} trades, "
          f"{final_summary.get('win_rate', 0)}% win, "
          f"{final_summary.get('total_r', 0):+.2f}R, "
          f"PF {final_summary.get('profit_factor', 0)}")

    # 7. Health checks + report
    print("[7/7] Health checks & report...")
    issues = health_check(bars, final_summary)
    for iss in issues:
        marker = "⚠ " if iss["severity"] == "warning" else ("✗ " if iss["severity"] == "critical" else "ℹ ")
        print(f"  {marker}{iss['msg']}")
    if not issues:
        print("  ✓ No issues")

    # Save winning config (only if actually profitable)
    if final_summary.get("total_r", 0) > 0 and final_summary.get("trades", 0) >= 5:
        save_winning_config(
            config={"pm_threshold": meta_dec.pm_threshold, "red_w": best["red_w"],
                    "orb_to": best["orb_to"], "ema_s": best["ema_s"]},
            stats=final_summary,
        )

    json_path, report = generate_report(
        bars, paths, meta_dec, calib_results, final_summary, issues, bt_final.equity_curve
    )
    elapsed = time.time() - start_time

    # One-screen summary
    print()
    print("=" * 65)
    print(f" AUTOPILOT COMPLETE   {elapsed:.1f}s")
    print("=" * 65)
    trades = final_summary.get("trades", 0)
    wr = final_summary.get("win_rate", 0)
    r = final_summary.get("total_r", 0)
    pf = final_summary.get("profit_factor", 0)
    dd = final_summary.get("max_drawdown_r", 0)
    wins = final_summary.get("wins", 0)
    losses = final_summary.get("losses", 0)
    bes = final_summary.get("breakevens", 0)
    resolved = wins + losses
    strike = (wins / resolved * 100) if resolved else 0
    print(f" Trades: {trades}  W:{wins}  L:{losses}  BE:{bes}")
    print(f" Win rate: {wr}%    Strike: {strike:.1f}% (excl. expirations)")
    print(f" Total R: {r:+.2f}   Profit factor: {pf}   Max DD: {dd}R")
    print(f" Firm confidence: {meta_dec.confidence}/100")
    print(f" Report: {json_path}")
    print("=" * 65)

    return report


def main():
    ap = argparse.ArgumentParser(description="Apex v2 Autopilot — single entrypoint")
    ap.add_argument("--mode", default="default",
                    choices=["default", "live", "calibrate"])
    ap.add_argument("--data", help="Override MNQ CSV path")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.mode == "live":
        print("Live mode: start webhook.py separately, autopilot generates the config it uses.")
        # Still run to generate/refresh the config
    result = run_autopilot(
        mode=args.mode,
        data_override=args.data,
        verbose=not args.quiet,
    )
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
