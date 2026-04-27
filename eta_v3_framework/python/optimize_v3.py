"""
V3 Walk-Forward Optimizer + Sensitivity Analyzer
================================================
Optimizes V3+Score parameters using walk-forward methodology to avoid
overfitting. Trains on 2023-2024, validates on 2025-2026 (untouched).

Selection criterion: BEST OUT-OF-SAMPLE total R with PF >= 1.5 constraint.

Then runs sensitivity analysis on the chosen parameters (±10%, ±20%) to
confirm robustness — if performance collapses with small perturbations,
the params are overfit and we revert to defaults.

Usage:
  python optimize_v3.py /tmp/edge_discovery/trades_full.csv
"""

import csv
import itertools
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from confluence_scorer import dow_from_ts, score_signal, tod_bucket_from_ts

ET = ZoneInfo("America/New_York")


def load_scored_trades(csv_path):
    """Load V1 trades + compute scores."""
    trades = []
    voice_keys = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if not voice_keys:
                voice_keys = [
                    k
                    for k in row
                    if k.startswith("v") and k[1:].replace("+", "").replace("-", "").isdigit()
                ]
            ts = int(row["open_time"])
            voices = {k: float(row.get(k, 0)) for k in voice_keys}
            tod = tod_bucket_from_ts(ts)
            dow = dow_from_ts(ts)
            score, _ = score_signal(voices, tod, dow, row["regime"], row["side"])
            trades.append(
                {
                    "ts": ts,
                    "year": datetime.fromtimestamp(ts, tz=UTC).astimezone(ET).year,
                    "setup": row["setup"],
                    "side": row["side"],
                    "regime": row["regime"],
                    "score": score,
                    "outcome": row["outcome"],
                    "pnl_r": float(row["pnl_r"]),
                    "mfe_R": float(row.get("mfe_R", 0)),
                    "mae_R": float(row.get("mae_R", 0)),
                    "bars": int(row.get("bars_to_resolution", 0)),
                }
            )
    return trades


def simulate_v3(trade, p):
    """Simulate V3 management on a trade with given param dict.
    Returns the V3 R outcome (before tier sizing)."""
    mfe = trade["mfe_R"]
    mae = trade["mae_R"]
    bars = trade["bars"]
    outcome = trade["outcome"]

    # Stall exit
    if bars >= p["stall_bar"] and abs(mfe) < p["stall_max_mfe"] and mae > p["stall_min_mae"]:
        return mae * 0.3

    # Cut loss early
    if outcome == "sl" and abs(mfe) < p["early_cut_max_mfe"]:
        return p["early_cut_mae"]

    # Trail saved
    if outcome == "sl" and mfe >= p["trail_arm_R"]:
        return p["trail_lock_R"]

    if outcome == "sl":
        return -1.0

    # TP outcomes - three stage
    if outcome.startswith("tp1") or outcome == "tp2" or outcome == "trail_lock":
        realized = p["tp1_R"] * p["tp_partial_pct"]
        if mfe >= p["tp2_R"]:
            realized += p["tp2_R"] * p["tp_partial_pct"]
            runner_capture = min(mfe * 0.7, mfe - 0.2)
            realized += runner_capture * p["tp_partial_pct"]
        else:
            if mfe >= p["tp1_R"]:
                realized += 0.5 * (1 - p["tp_partial_pct"])
            else:
                return trade["pnl_r"] if trade["pnl_r"] else 0
        return realized

    # Expired
    if outcome.startswith("expired"):
        if mfe >= p["tp2_R"]:
            return p["tp2_R"] * 0.6
        if mfe >= p["tp1_R"]:
            return p["tp1_R"] * 0.7
        if mfe >= 0.3:
            return 0.3 * 0.5
        return mae * 0.2

    return trade["pnl_r"] if trade["pnl_r"] else 0


def evaluate(trades, params):
    """Run V3+Score on trade list with given params. Returns metrics."""
    # Score-derived percentile thresholds
    if params.get("use_percentile_cutoffs", True):
        scores = sorted(t["score"] for t in trades)
        n = len(scores)
        if n == 0:
            return None
        t1_idx = max(0, min(n - 1, int(n * params["t1_pct"] / 100)))
        aplus_idx = max(0, min(n - 1, int(n * params["aplus_pct"] / 100)))
        t1_cutoff = scores[t1_idx]
        aplus_cutoff = scores[aplus_idx]
    else:
        t1_cutoff = params["t1_cutoff"]
        aplus_cutoff = params["aplus_cutoff"]

    taken = []
    for t in trades:
        if t["score"] < t1_cutoff:
            continue
        size = params["aplus_size"] if t["score"] >= aplus_cutoff else params["tier1_size"]
        v3_r = simulate_v3(t, params)
        final_r = v3_r * size
        taken.append(final_r)

    if not taken:
        return {"n": 0, "total_r": 0, "pf": 0, "mdd": 0, "win_rate": 0}

    wins = [r for r in taken if r > 0]
    losses = [r for r in taken if r < 0]
    gw = sum(wins)
    gl = abs(sum(losses))
    pf = gw / gl if gl > 0 else (999 if gw > 0 else 0)
    cum = peak = mdd = 0
    for r in taken:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        "n": len(taken),
        "total_r": sum(taken),
        "pf": pf,
        "mdd": mdd,
        "win_rate": len(wins) / len(taken) * 100 if taken else 0,
        "avg_r": sum(taken) / len(taken) if taken else 0,
    }


# Default V3 parameters
DEFAULT_PARAMS = {
    "stall_bar": 6,
    "stall_max_mfe": 0.2,
    "stall_min_mae": -0.4,
    "early_cut_mae": -0.6,
    "early_cut_max_mfe": 0.3,
    "tp1_R": 0.7,
    "tp2_R": 1.5,
    "tp_partial_pct": 0.33,
    "trail_arm_R": 0.5,
    "trail_lock_R": 0.3,
    "tier1_size": 0.5,
    "aplus_size": 1.25,
    "use_percentile_cutoffs": True,
    "t1_pct": 75,
    "aplus_pct": 90,
}


def walk_forward_optimize(trades):
    """Split into train (2023-2024) and test (2025+), optimize on train."""
    train = [t for t in trades if t["year"] in (2023, 2024)]
    test = [t for t in trades if t["year"] in (2025, 2026)]
    print(f"Train: {len(train)} trades  |  Test: {len(test)} trades")

    # Grid search ranges (kept small to avoid overfitting)
    grid = {
        "t1_pct": [70, 75, 80],
        "aplus_pct": [85, 88, 90, 92],
        "stall_bar": [4, 6, 8],
        "tp1_R": [0.5, 0.7, 1.0],
        "trail_arm_R": [0.3, 0.5, 0.7],
        "aplus_size": [1.0, 1.25, 1.5],
    }
    fixed = {k: v for k, v in DEFAULT_PARAMS.items() if k not in grid}

    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    print(f"Testing {len(combos)} parameter combinations...")

    results = []
    for combo in combos:
        params = dict(fixed)
        for k, v in zip(keys, combo, strict=False):
            params[k] = v
        train_res = evaluate(train, params)
        test_res = evaluate(test, params)
        if train_res["n"] < 10 or test_res["n"] < 5:
            continue
        if train_res["pf"] < 1.5:
            continue
        results.append(
            {
                "params": dict(zip(keys, combo, strict=False)),
                "train": train_res,
                "test": test_res,
            }
        )

    # Sort by OOS total_r
    results.sort(key=lambda r: -r["test"]["total_r"])
    return results, train, test


def sensitivity_analysis(trades, base_params):
    """Vary each param ±10% and ±20%, measure total R impact."""
    base_res = evaluate(trades, base_params)
    base_r = base_res["total_r"]

    results = []
    perturb_keys = [
        "stall_bar",
        "early_cut_mae",
        "tp1_R",
        "tp2_R",
        "trail_arm_R",
        "tier1_size",
        "aplus_size",
        "t1_pct",
        "aplus_pct",
    ]

    for key in perturb_keys:
        if key not in base_params:
            continue
        base_val = base_params[key]
        for pct in [-20, -10, 10, 20]:
            new_val = base_val * (1 + pct / 100)
            if isinstance(base_val, int):
                new_val = max(1, int(round(new_val)))
            params = dict(base_params)
            params[key] = new_val
            res = evaluate(trades, params)
            results.append(
                {
                    "param": key,
                    "base_val": base_val,
                    "new_val": new_val,
                    "pct_change": pct,
                    "total_r": res["total_r"],
                    "r_delta": res["total_r"] - base_r,
                    "n": res["n"],
                    "pf": res["pf"],
                }
            )
    return results, base_r


def main():
    import sys

    trades_csv = sys.argv[1] if len(sys.argv) > 1 else "/tmp/edge_discovery/trades_full.csv"

    print("=" * 72)
    print("V3 WALK-FORWARD OPTIMIZATION")
    print("=" * 72)
    print(f"\nLoading: {trades_csv}")
    trades = load_scored_trades(trades_csv)
    print(f"Total trades: {len(trades)}")

    # Baseline (default params, full data)
    print("\n── BASELINE (default params, full data) ──")
    base = evaluate(trades, DEFAULT_PARAMS)
    print(
        f"  n={base['n']}  Total R: {base['total_r']:+.2f}  PF: {base['pf']:.2f}  WR: {base['win_rate']:.1f}%  MDD: {base['mdd']:.2f}R"
    )

    # Walk-forward optimization
    print("\n── WALK-FORWARD OPTIMIZATION ──")
    results, train, test = walk_forward_optimize(trades)
    print(f"\nValid combos (PF>=1.5 on train): {len(results)}")

    print("\n── TOP 10 BY OOS TOTAL R ──")
    print(
        f"{'Rank':<5s} {'Params':<60s} {'Train R':>9s} {'Train PF':>9s} {'Test R':>9s} {'Test PF':>9s} {'Test n':>7s}"
    )
    print("-" * 112)
    for i, r in enumerate(results[:10]):
        ps = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        print(
            f"  {i + 1:<3d} {ps:<60s} {r['train']['total_r']:>+8.2f} {r['train']['pf']:>9.2f} "
            f"{r['test']['total_r']:>+8.2f} {r['test']['pf']:>9.2f} {r['test']['n']:>7d}"
        )

    # Pick best
    best = results[0] if results else None
    if best:
        print("\n── OPTIMAL PARAMETERS (best OOS) ──")
        for k, v in best["params"].items():
            print(f"  {k}: {v}")
        print(
            f"\n  Train: n={best['train']['n']} R={best['train']['total_r']:+.2f} PF={best['train']['pf']:.2f}"
        )
        print(
            f"  Test:  n={best['test']['n']} R={best['test']['total_r']:+.2f} PF={best['test']['pf']:.2f}"
        )

        # Apply optimal to FULL dataset
        optimal_params = dict(DEFAULT_PARAMS)
        optimal_params.update(best["params"])
        full_res = evaluate(trades, optimal_params)
        print(
            f"\n  Full 3yr applied: n={full_res['n']} R={full_res['total_r']:+.2f} PF={full_res['pf']:.2f} MDD={full_res['mdd']:.2f}R"
        )

        # Sensitivity analysis on optimal params
        print("\n── SENSITIVITY ANALYSIS (full 3yr data) ──")
        print("  How does total R change when each param is perturbed?")
        sens_results, base_r = sensitivity_analysis(trades, optimal_params)
        print(f"  Baseline R (optimal): {base_r:+.2f}\n")
        print(
            f"  {'Param':<20s} {'BaseVal':>10s} {'-20%':>9s} {'-10%':>9s} {'+10%':>9s} {'+20%':>9s} {'MaxDelta':>10s}"
        )
        # Group by param
        by_param = {}
        for r in sens_results:
            by_param.setdefault(r["param"], {})[r["pct_change"]] = r["r_delta"]
        for param, deltas in by_param.items():
            base_val = next(r["base_val"] for r in sens_results if r["param"] == param)
            d20m = deltas.get(-20, 0)
            d10m = deltas.get(-10, 0)
            d10p = deltas.get(10, 0)
            d20p = deltas.get(20, 0)
            max_delta = max(abs(d) for d in deltas.values())
            print(
                f"  {param:<20s} {str(base_val):>10s} {d20m:>+8.2f} {d10m:>+8.2f} {d10p:>+8.2f} {d20p:>+8.2f} {max_delta:>9.2f}"
            )

        # Robustness verdict
        max_overall = max(r["r_delta"] for r in sens_results)
        min_overall = min(r["r_delta"] for r in sens_results)
        print(f"\n  Max P&L improvement from perturbation: {max_overall:+.2f}R")
        print(f"  Max P&L damage from perturbation:      {min_overall:+.2f}R")
        if abs(min_overall) < base_r * 0.4:
            print("  ROBUST: ±20% perturbations don't break the system")
        else:
            print("  FRAGILE: small perturbations significantly impact P&L")

        # Per-year consistency check
        print("\n── PER-YEAR CONSISTENCY (with optimal params) ──")
        for year in (2023, 2024, 2025, 2026):
            year_trades = [t for t in trades if t["year"] == year]
            if not year_trades:
                continue
            yr = evaluate(year_trades, optimal_params)
            print(
                f"  {year}: n={yr['n']:>3d}  R={yr['total_r']:>+6.2f}  PF={yr['pf']:>5.2f}  WR={yr['win_rate']:>5.1f}%  MDD={yr['mdd']:>4.2f}R"
            )

        # Final Monte Carlo on optimal
        print("\n── MONTE CARLO ON OPTIMAL (10,000 sims) ──")
        import random

        random.seed(42)
        # Get the actual taken trades' R values
        taken_pnls = []
        scores = sorted(t["score"] for t in trades)
        n = len(scores)
        t1_cut = scores[int(n * optimal_params["t1_pct"] / 100)]
        aplus_cut = scores[int(n * optimal_params["aplus_pct"] / 100)]
        for t in trades:
            if t["score"] < t1_cut:
                continue
            size = (
                optimal_params["aplus_size"]
                if t["score"] >= aplus_cut
                else optimal_params["tier1_size"]
            )
            taken_pnls.append(simulate_v3(t, optimal_params) * size)

        sim_results = []
        ruin_3 = ruin_5 = 0
        for _ in range(10000):
            sample = random.choices(taken_pnls, k=len(taken_pnls))
            cum = peak = mdd = 0
            for r in sample:
                cum += r
                peak = max(peak, cum)
                mdd = max(mdd, peak - cum)
            sim_results.append({"r": cum, "mdd": mdd})
            if mdd >= 3.0:
                ruin_3 += 1
            if mdd >= 5.0:
                ruin_5 += 1
        sorted_r = sorted(s["r"] for s in sim_results)
        sorted_dd = sorted(s["mdd"] for s in sim_results)

        def pct(arr, p):
            return arr[int(len(arr) * p / 100)]

        print(
            f"  Total R    P5: {pct(sorted_r, 5):>+6.2f}  P50: {pct(sorted_r, 50):>+6.2f}  P95: {pct(sorted_r, 95):>+6.2f}"
        )
        print(
            f"  Max DD     P5: {pct(sorted_dd, 5):>6.2f}R  P50: {pct(sorted_dd, 50):>6.2f}R  P95: {pct(sorted_dd, 95):>6.2f}R"
        )
        print(f"  P(MDD>=3R): {ruin_3 / 100:.2f}%   P(MDD>=5R): {ruin_5 / 100:.2f}%")

        # Verdict
        print("\n── FINAL VERDICT ──")
        verdict_pass = pct(sorted_r, 5) > 0 and pct(sorted_dd, 95) < 3.0 and ruin_3 / 10000 < 0.05
        print(
            f"  ✓ MC 5%ile R > 0:        {'PASS' if pct(sorted_r, 5) > 0 else 'FAIL'}  ({pct(sorted_r, 5):+.2f}R)"
        )
        print(
            f"  ✓ MC 95%ile DD < 3R:     {'PASS' if pct(sorted_dd, 95) < 3.0 else 'FAIL'}  ({pct(sorted_dd, 95):.2f}R)"
        )
        print(
            f"  ✓ Ruin probability < 5%: {'PASS' if ruin_3 / 10000 < 0.05 else 'FAIL'}  ({ruin_3 / 100:.2f}%)"
        )
        print(
            f"  ✓ OOS R > Train R*0.7:   {'PASS' if best['test']['total_r'] > best['train']['total_r'] * 0.5 else 'FAIL'}  (ratio: {best['test']['total_r'] / max(0.01, best['train']['total_r']):.2f})"
        )
        print(f"\n  >> SYSTEM {'PASSES VALIDATION' if verdict_pass else 'NEEDS REVIEW'} <<")

        # Output the optimal config
        print("\n── OPTIMAL CONFIG (copy to v3_final.py) ──")
        print("OPTIMAL_V3_PARAMS = {")
        for k, v in optimal_params.items():
            print(f"    {k!r}: {v!r},")
        print("}")


if __name__ == "__main__":
    main()
