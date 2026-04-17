"""
Edge Discovery — V1 LOCKED Analysis Engine
==========================================
The Firm acts as analyst. Runs locked V1 logic on full historical data,
captures rich per-trade attribution, decomposes performance across every
dimension to find where edge actually lives.

OUTPUTS:
  trades_full.csv     - One row per trade with full feature attribution
  edge_buckets.csv    - Cross-tabbed performance by setup×TOD×regime×voice signature
  payoff_analysis.csv - MFE/MAE distributions, expiration deep-dive
  edge_findings.md    - Human-readable edge specification

This is STEP 2 + STEP 3 + STEP 4 of the Edge Discovery Protocol combined.

Usage:
  python edge_discovery.py /tmp/historical/nq_5m.csv --pm 25
"""

import argparse
import csv
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from firm_engine import FirmConfig, Bar
from backtest import Backtester, V1DetectorConfig, load_csv

ET = ZoneInfo("America/New_York")


def tod_bucket(ts):
    """Classify timestamp into time-of-day bucket."""
    et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)
    m = et.hour * 60 + et.minute
    if et.weekday() >= 5: return "weekend"
    if m < 9*60+30: return "premarket"
    if m < 10*60+30: return "open_30min"      # 9:30-10:30
    if m < 11*60+30: return "mid_am"          # 10:30-11:30
    if m < 13*60+30: return "lunch"           # 11:30-13:30
    if m < 14*60+30: return "early_pm"        # 13:30-14:30
    if m < 15*60+30: return "power_hour"      # 14:30-15:30
    if m < 16*60: return "moc"                # 15:30-16:00
    return "after_hours"


def dow_name(ts):
    et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return names[et.weekday()]


def voice_signature(voices, threshold=40):
    """Identify which voices are 'firing' at signal time."""
    firing = []
    for vname, vscore in voices.items():
        if abs(vscore) >= threshold:
            firing.append(f"{vname}{'+' if vscore > 0 else '-'}")
    return "|".join(sorted(firing)) if firing else "none"


def run_with_capture(bars, pm):
    """Run V1 backtest and return enriched trade list."""
    cfg = FirmConfig(pm_threshold=pm, require_setup=True)
    det_cfg = V1DetectorConfig()
    bt = Backtester(cfg=cfg, detector_cfg=det_cfg)
    bt.run(bars)
    return bt.trades, bt


def aggregate_bucket(trades, group_fn, min_n=5):
    """Compute aggregate stats per bucket. Returns sorted list of (bucket, stats)."""
    buckets = defaultdict(list)
    for t in trades:
        key = group_fn(t)
        buckets[key].append(t)

    results = []
    for key, ts in buckets.items():
        if len(ts) < min_n:
            continue
        wins = [t for t in ts if t.pnl_r > 0]
        losses = [t for t in ts if t.pnl_r < 0]
        bes = [t for t in ts if t.pnl_r == 0]
        n_resolved = len(wins) + len(losses)
        total_r = sum(t.pnl_r for t in ts)
        avg_r = total_r / len(ts)
        win_rate = len(wins) / len(ts) * 100
        strike = len(wins) / n_resolved * 100 if n_resolved > 0 else 0
        gross_w = sum(t.pnl_r for t in wins)
        gross_l = abs(sum(t.pnl_r for t in losses))
        pf = gross_w / gross_l if gross_l > 0 else (999.0 if gross_w > 0 else 0)
        avg_mfe = statistics.mean(t.mfe_R for t in ts) if all(hasattr(t, 'mfe_R') for t in ts) else 0
        avg_mae = statistics.mean(t.mae_R for t in ts) if all(hasattr(t, 'mae_R') for t in ts) else 0
        results.append({
            "bucket": str(key),
            "n": len(ts),
            "wins": len(wins),
            "losses": len(losses),
            "be": len(bes),
            "win_rate": round(win_rate, 1),
            "strike": round(strike, 1),
            "avg_r": round(avg_r, 3),
            "total_r": round(total_r, 2),
            "pf": round(pf, 2) if pf < 999 else "inf",
            "avg_mfe": round(avg_mfe, 2),
            "avg_mae": round(avg_mae, 2),
        })
    return sorted(results, key=lambda x: -x["avg_r"])


def write_trades_csv(trades, path):
    """Write trade log with full attribution to CSV."""
    with open(path, 'w', newline='') as f:
        if not trades:
            return
        # Get all voice keys from first trade
        voice_keys = sorted(trades[0].voices.keys()) if trades[0].voices else []
        cols = ['open_time', 'datetime_et', 'tod_bucket', 'dow', 'setup', 'side',
                'entry', 'sl', 'tp1', 'tp2', 'sl_dist', 'pm_final', 'quant', 'red',
                'regime', 'outcome', 'pnl_r', 'mfe_R', 'mae_R', 'bars_to_resolution',
                'size_pct', 'tp1_filled', 'voice_signature'] + voice_keys
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for t in trades:
            et_dt = datetime.fromtimestamp(t.open_time, tz=timezone.utc).astimezone(ET)
            row = {
                'open_time': t.open_time,
                'datetime_et': et_dt.strftime("%Y-%m-%d %H:%M"),
                'tod_bucket': tod_bucket(t.open_time),
                'dow': dow_name(t.open_time),
                'setup': t.setup, 'side': t.side,
                'entry': t.entry, 'sl': t.sl, 'tp1': t.tp1, 'tp2': t.tp2,
                'sl_dist': t.sl_dist,
                'pm_final': t.pm_final, 'quant': t.quant, 'red': t.red,
                'regime': t.regime, 'outcome': t.outcome, 'pnl_r': t.pnl_r,
                'mfe_R': getattr(t, 'mfe_R', 0),
                'mae_R': getattr(t, 'mae_R', 0),
                'bars_to_resolution': getattr(t, 'bars_to_resolution', 0),
                'size_pct': t.size_pct,
                'tp1_filled': t.tp1_filled,
                'voice_signature': voice_signature(t.voices),
            }
            for vk in voice_keys:
                row[vk] = round(t.voices.get(vk, 0), 1)
            writer.writerow(row)


def expiration_analysis(trades):
    """Deep dive into expired trades — could time stops or dynamic targets save them?"""
    expired = [t for t in trades if t.outcome.startswith("expired")]
    if not expired:
        return {}
    # MFE > 0.5R but expired = winners we left on the table
    saved_at_05 = [t for t in expired if getattr(t, 'mfe_R', 0) >= 0.5]
    saved_at_10 = [t for t in expired if getattr(t, 'mfe_R', 0) >= 1.0]
    avg_mfe = statistics.mean(getattr(t, 'mfe_R', 0) for t in expired)
    avg_mae = statistics.mean(getattr(t, 'mae_R', 0) for t in expired)
    return {
        "n_expired": len(expired),
        "pct_of_total": round(len(expired) / len(trades) * 100, 1),
        "n_with_mfe_above_0.5R": len(saved_at_05),
        "n_with_mfe_above_1.0R": len(saved_at_10),
        "avg_mfe_R": round(avg_mfe, 3),
        "avg_mae_R": round(avg_mae, 3),
        "potential_R_saved_at_0.5R": round(0.5 * len(saved_at_05), 2),
        "potential_R_saved_at_1.0R": round(1.0 * len(saved_at_10), 2),
    }


def write_findings_md(trades, by_setup, by_tod, by_regime, by_setup_tod, by_voice_sig, exp_analysis, total_r, pm):
    """Generate human-readable edge findings document."""
    lines = []
    lines.append(f"# Edge Discovery Findings — V1 LOCKED at PM={pm}")
    lines.append(f"")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"")
    lines.append(f"## Sample")
    lines.append(f"- Total trades: {len(trades)}")
    lines.append(f"- Total R: {total_r:+.2f}")
    lines.append(f"- Avg R/trade: {total_r/len(trades) if trades else 0:+.4f}")
    lines.append(f"")

    lines.append(f"## By Setup")
    lines.append(f"")
    lines.append(f"| Setup | n | Win% | Strike% | Avg R | Total R | PF | Avg MFE | Avg MAE |")
    lines.append(f"|-------|---|------|---------|-------|---------|-----|---------|---------|")
    for r in by_setup:
        lines.append(f"| {r['bucket']} | {r['n']} | {r['win_rate']} | {r['strike']} | {r['avg_r']:+.3f} | {r['total_r']:+.2f} | {r['pf']} | {r['avg_mfe']} | {r['avg_mae']} |")
    lines.append(f"")

    lines.append(f"## By Time of Day (RTH only, excluding pre/after market)")
    lines.append(f"")
    lines.append(f"| TOD | n | Win% | Strike% | Avg R | Total R | PF |")
    lines.append(f"|-----|---|------|---------|-------|---------|-----|")
    for r in by_tod:
        if r['bucket'] in ('weekend', 'premarket', 'after_hours'):
            continue
        lines.append(f"| {r['bucket']} | {r['n']} | {r['win_rate']} | {r['strike']} | {r['avg_r']:+.3f} | {r['total_r']:+.2f} | {r['pf']} |")
    lines.append(f"")

    lines.append(f"## By Regime")
    lines.append(f"")
    lines.append(f"| Regime | n | Win% | Strike% | Avg R | Total R | PF |")
    lines.append(f"|--------|---|------|---------|-------|---------|-----|")
    for r in by_regime:
        lines.append(f"| {r['bucket']} | {r['n']} | {r['win_rate']} | {r['strike']} | {r['avg_r']:+.3f} | {r['total_r']:+.2f} | {r['pf']} |")
    lines.append(f"")

    lines.append(f"## TOP 15 Setup × Time-of-Day Combinations (positive expectancy, n>=5)")
    lines.append(f"")
    lines.append(f"| Bucket | n | Win% | Strike% | Avg R | Total R | PF |")
    lines.append(f"|--------|---|------|---------|-------|---------|-----|")
    for r in by_setup_tod[:15]:
        if r['avg_r'] <= 0:
            continue
        lines.append(f"| {r['bucket']} | {r['n']} | {r['win_rate']} | {r['strike']} | {r['avg_r']:+.3f} | {r['total_r']:+.2f} | {r['pf']} |")
    lines.append(f"")

    lines.append(f"## BOTTOM 10 Setup × TOD (negative expectancy — avoid these)")
    lines.append(f"")
    lines.append(f"| Bucket | n | Win% | Strike% | Avg R | Total R | PF |")
    lines.append(f"|--------|---|------|---------|-------|---------|-----|")
    for r in sorted(by_setup_tod, key=lambda x: x['avg_r'])[:10]:
        if r['avg_r'] >= 0:
            continue
        lines.append(f"| {r['bucket']} | {r['n']} | {r['win_rate']} | {r['strike']} | {r['avg_r']:+.3f} | {r['total_r']:+.2f} | {r['pf']} |")
    lines.append(f"")

    lines.append(f"## TOP 10 Voice Signatures (pattern of voices firing together)")
    lines.append(f"")
    lines.append(f"| Voice signature | n | Win% | Strike% | Avg R | Total R |")
    lines.append(f"|-----------------|---|------|---------|-------|---------|")
    for r in by_voice_sig[:10]:
        lines.append(f"| {r['bucket']} | {r['n']} | {r['win_rate']} | {r['strike']} | {r['avg_r']:+.3f} | {r['total_r']:+.2f} |")
    lines.append(f"")

    lines.append(f"## Expiration Deep Dive")
    lines.append(f"")
    if exp_analysis:
        lines.append(f"- Total expired trades: {exp_analysis['n_expired']} ({exp_analysis['pct_of_total']}% of all trades)")
        lines.append(f"- Avg MFE while open: {exp_analysis['avg_mfe_R']}R")
        lines.append(f"- Avg MAE while open: {exp_analysis['avg_mae_R']}R")
        lines.append(f"- Expired trades that hit MFE >= 0.5R: {exp_analysis['n_with_mfe_above_0.5R']}")
        lines.append(f"- Expired trades that hit MFE >= 1.0R: {exp_analysis['n_with_mfe_above_1.0R']}")
        lines.append(f"- **Potential R recovered with 0.5R partial-take rule**: +{exp_analysis['potential_R_saved_at_0.5R']}R")
        lines.append(f"- **Potential R recovered with 1.0R partial-take rule**: +{exp_analysis['potential_R_saved_at_1.0R']}R")
    lines.append(f"")

    # Findings interpretation
    lines.append(f"## What this tells us")
    lines.append(f"")
    best_setup = by_setup[0] if by_setup else None
    best_tod = next((r for r in by_tod if r['bucket'] not in ('weekend', 'premarket', 'after_hours') and r['avg_r'] > 0), None)
    best_combo = next((r for r in by_setup_tod if r['avg_r'] > 0.05 and r['n'] >= 10), None)
    if best_setup:
        lines.append(f"- Best-performing setup: **{best_setup['bucket']}** at {best_setup['avg_r']:+.3f}R/trade ({best_setup['n']} trades)")
    if best_tod:
        lines.append(f"- Best time-of-day: **{best_tod['bucket']}** at {best_tod['avg_r']:+.3f}R/trade ({best_tod['n']} trades)")
    if best_combo:
        lines.append(f"- Best setup × TOD combination with statistical weight: **{best_combo['bucket']}** at {best_combo['avg_r']:+.3f}R/trade ({best_combo['n']} trades, {best_combo['strike']}% strike)")
    lines.append(f"")
    if exp_analysis and exp_analysis['potential_R_saved_at_0.5R'] > 1:
        lines.append(f"- **Time-stop opportunity**: A 0.5R partial-take rule on stalled trades would recover +{exp_analysis['potential_R_saved_at_0.5R']}R that V1 currently leaves on the table")
    lines.append(f"")
    lines.append(f"## Next Step")
    lines.append(f"")
    lines.append(f"Take the TOP buckets above (positive expectancy + n>=10) and use them as the V2 spec.")
    lines.append(f"Implement them as orthogonal filters (require setup ∈ best_setups AND time ∈ best_TODs AND regime ∈ best_regimes).")
    lines.append(f"That replaces the PM gate with data-derived confluence.")

    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Edge Discovery — V1 LOCKED Analysis")
    p.add_argument("csv", help="OHLCV CSV (3 years recommended)")
    p.add_argument("--pm", type=float, default=25.0, help="V1 PM threshold (default 25)")
    p.add_argument("--out-dir", default="/tmp/edge_discovery", help="Output directory")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {args.csv}...")
    bars = load_csv(args.csv)
    print(f"  {len(bars):,} bars  ({datetime.fromtimestamp(bars[0].time, tz=timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(bars[-1].time, tz=timezone.utc):%Y-%m-%d})")

    print(f"\nRunning V1 (LOCKED) at PM={args.pm}...")
    trades, bt = run_with_capture(bars, args.pm)
    print(f"  Generated {len(trades)} trades")
    if not trades:
        print("No trades. Exiting.")
        return

    # Trade log
    print(f"\nWriting trade log...")
    write_trades_csv(trades, out_dir / "trades_full.csv")
    print(f"  → {out_dir / 'trades_full.csv'}")

    # Aggregations
    print(f"\nDecomposing performance...")
    by_setup = aggregate_bucket(trades, lambda t: t.setup, min_n=3)
    by_tod = aggregate_bucket(trades, lambda t: tod_bucket(t.open_time), min_n=3)
    by_dow = aggregate_bucket(trades, lambda t: dow_name(t.open_time), min_n=3)
    by_regime = aggregate_bucket(trades, lambda t: t.regime, min_n=3)
    by_outcome = aggregate_bucket(trades, lambda t: t.outcome, min_n=3)
    by_setup_tod = aggregate_bucket(trades, lambda t: f"{t.setup}_{tod_bucket(t.open_time)}", min_n=5)
    by_setup_regime = aggregate_bucket(trades, lambda t: f"{t.setup}_{t.regime}", min_n=5)
    by_voice_sig = aggregate_bucket(trades, lambda t: voice_signature(t.voices), min_n=5)
    by_dow_setup = aggregate_bucket(trades, lambda t: f"{dow_name(t.open_time)}_{t.setup}", min_n=5)

    # Write all bucket CSVs
    def write_bucket_csv(buckets, path, dim_name):
        with open(path, 'w', newline='') as f:
            if not buckets:
                return
            writer = csv.DictWriter(f, fieldnames=[dim_name] + list(buckets[0].keys())[1:])
            writer.writeheader()
            for r in buckets:
                row = dict(r)
                row[dim_name] = row.pop('bucket')
                writer.writerow(row)

    write_bucket_csv(by_setup, out_dir / "by_setup.csv", "setup")
    write_bucket_csv(by_tod, out_dir / "by_tod.csv", "tod")
    write_bucket_csv(by_dow, out_dir / "by_dow.csv", "dow")
    write_bucket_csv(by_regime, out_dir / "by_regime.csv", "regime")
    write_bucket_csv(by_outcome, out_dir / "by_outcome.csv", "outcome")
    write_bucket_csv(by_setup_tod, out_dir / "by_setup_tod.csv", "setup_tod")
    write_bucket_csv(by_setup_regime, out_dir / "by_setup_regime.csv", "setup_regime")
    write_bucket_csv(by_voice_sig, out_dir / "by_voice_sig.csv", "voice_sig")
    write_bucket_csv(by_dow_setup, out_dir / "by_dow_setup.csv", "dow_setup")
    print(f"  → 9 bucket CSVs written")

    # Expiration analysis
    exp = expiration_analysis(trades)

    # Print headline findings
    print(f"\n{'='*72}")
    print(f"HEADLINE FINDINGS")
    print(f"{'='*72}")
    print(f"\nBy Setup:")
    print(f"  {'Setup':<10s} {'n':>5s} {'Win%':>6s} {'Strike':>7s} {'AvgR':>7s} {'TotR':>7s} {'PF':>6s}")
    for r in by_setup:
        print(f"  {r['bucket']:<10s} {r['n']:>5d} {r['win_rate']:>5.1f}% {r['strike']:>6.1f}% {r['avg_r']:>+7.3f} {r['total_r']:>+7.2f} {str(r['pf']):>6s}")

    print(f"\nBy Time of Day (RTH):")
    print(f"  {'TOD':<14s} {'n':>5s} {'Win%':>6s} {'Strike':>7s} {'AvgR':>7s} {'TotR':>7s} {'PF':>6s}")
    for r in by_tod:
        if r['bucket'] in ('weekend', 'premarket', 'after_hours'):
            continue
        print(f"  {r['bucket']:<14s} {r['n']:>5d} {r['win_rate']:>5.1f}% {r['strike']:>6.1f}% {r['avg_r']:>+7.3f} {r['total_r']:>+7.2f} {str(r['pf']):>6s}")

    print(f"\nBy Regime:")
    print(f"  {'Regime':<12s} {'n':>5s} {'Win%':>6s} {'Strike':>7s} {'AvgR':>7s} {'TotR':>7s} {'PF':>6s}")
    for r in by_regime:
        print(f"  {r['bucket']:<12s} {r['n']:>5d} {r['win_rate']:>5.1f}% {r['strike']:>6.1f}% {r['avg_r']:>+7.3f} {r['total_r']:>+7.2f} {str(r['pf']):>6s}")

    print(f"\nTOP 10 Setup × TOD (positive expectancy, n>=5):")
    print(f"  {'Bucket':<22s} {'n':>5s} {'Strike':>7s} {'AvgR':>7s} {'TotR':>7s}")
    shown = 0
    for r in by_setup_tod:
        if r['avg_r'] <= 0 or shown >= 10:
            continue
        print(f"  {r['bucket']:<22s} {r['n']:>5d} {r['strike']:>6.1f}% {r['avg_r']:>+7.3f} {r['total_r']:>+7.2f}")
        shown += 1

    print(f"\nBOTTOM 5 Setup × TOD (negative expectancy):")
    for r in sorted(by_setup_tod, key=lambda x: x['avg_r'])[:5]:
        if r['avg_r'] >= 0:
            continue
        print(f"  {r['bucket']:<22s} {r['n']:>5d} {r['strike']:>6.1f}% {r['avg_r']:>+7.3f} {r['total_r']:>+7.2f}")

    if exp:
        print(f"\nExpiration deep-dive:")
        print(f"  {exp['n_expired']} expired trades ({exp['pct_of_total']}% of all)")
        print(f"  Avg MFE while open: {exp['avg_mfe_R']}R | Avg MAE: {exp['avg_mae_R']}R")
        print(f"  Recoverable with 0.5R partial-take: +{exp['potential_R_saved_at_0.5R']}R")
        print(f"  Recoverable with 1.0R partial-take: +{exp['potential_R_saved_at_1.0R']}R")

    # Write findings document
    total_r = sum(t.pnl_r for t in trades)
    findings_md = write_findings_md(trades, by_setup, by_tod, by_regime, by_setup_tod, by_voice_sig, exp, total_r, args.pm)
    (out_dir / "edge_findings.md").write_text(findings_md)
    print(f"\n→ Full findings: {out_dir / 'edge_findings.md'}")
    print(f"→ Trade log:     {out_dir / 'trades_full.csv'}")
    print(f"→ Bucket CSVs:   {out_dir}/by_*.csv")


if __name__ == "__main__":
    main()
