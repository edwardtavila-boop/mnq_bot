#!/usr/bin/env python3
"""Edge forensics — prove or disprove the 200-day edge claim.

Context:
  The OW validation run (Batch 11A) reported $0.00 PnL on the last 80 days
  of the 200-day sample and flagged it as "inconclusive." Phase 11 deferred
  the question by shipping OW weights as opt-in. That deferral has been
  carried for multiple batches and is now the single biggest unknown in the
  framework. This script resolves the deferral by forcing each claimed edge
  variant through five decomposition lenses:

    1. QUARTERLY EQUITY CURVE   — split the trade log into 4 chronological
                                   buckets, compute per-bucket expectancy +
                                   Sharpe. If all positive PnL sits in
                                   bucket 1, the edge is regime-bound.
    2. REGIME CONDITIONAL       — use the `regime` column already baked
                                   into each trade CSV to cross-tab PnL.
                                   Identifies which regimes are
                                   load-bearing vs dead weight.
    3. DEFLATED SHARPE (DSR)    — Bailey & López de Prado's correction for
                                   multiple testing. With N variants swept
                                   through walk-forward + OW recalibration,
                                   raw Sharpe overstates the edge.
    4. BOOTSTRAP CI             — stratified block bootstrap (10k iters) on
                                   per-trade PnL. If the 95% CI on total
                                   PnL includes zero, the variant is
                                   statistically unproven.
    5. COST SENSITIVITY         — reprice each trade at $-1.74, $-5, $-10
                                   per round-trip friction. Tells you how
                                   much of the claimed edge survives under
                                   realistic live costs.

Inputs (all pre-existing in the repo):
  reports/backtest_real_trades.csv          — r0_real_baseline + siblings
  reports/backtest_real_v3_trades.csv       — v3 pullback variants
  reports/backtest_real_ensemble_trades.csv — ensemble combinations
  reports/backtest_real_micro_trades.csv    — 1m micro-refinement variants

Output:
  reports/edge_forensics.md — full tearsheet with per-variant verdicts.

Usage:
  python scripts/edge_forensics.py
  python scripts/edge_forensics.py --buckets 4 --bootstrap 10000
  python scripts/edge_forensics.py --top 5     # limit to top-5 variants by trade count
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS = REPO_ROOT / "reports"
OUTPUT = REPORTS / "edge_forensics.md"
OUTPUT_JSON = REPORTS / "edge_forensics.json"

TRADE_CSVS = [
    REPORTS / "backtest_real_trades.csv",
    REPORTS / "backtest_real_v3_trades.csv",
    REPORTS / "backtest_real_ensemble_trades.csv",
    REPORTS / "backtest_real_micro_trades.csv",
]

# Reference costs to stress against ---------------------------------------
# $-1.74 = measured shadow-parity slippage (BUG-003, Batch 4C).
# $-5    = pessimistic but plausible for a single MNQ contract round trip.
# $-10   = stressed / market-order / wide-spread scenario.
COST_SCENARIOS_PER_TRADE = (-1.74, -5.0, -10.0)


# -------------------------------------------------------------------------
# Core statistics
# -------------------------------------------------------------------------
def _sharpe_annualized(daily_returns: np.ndarray) -> float:
    """Daily-PnL Sharpe, annualized to 252 trading days.

    Returns 0.0 if fewer than 2 daily obs or zero stdev (undefined Sharpe).
    """
    if daily_returns.size < 2:
        return 0.0
    std = float(np.std(daily_returns, ddof=1))
    if std == 0.0:
        return 0.0
    mean = float(np.mean(daily_returns))
    return mean / std * math.sqrt(252.0)


def _t_stat(x: np.ndarray) -> float:
    """One-sample t-stat for H0: mean == 0."""
    if x.size < 2:
        return 0.0
    std = float(np.std(x, ddof=1))
    if std == 0.0:
        return 0.0
    return float(np.mean(x)) / (std / math.sqrt(x.size))


def _deflated_sharpe(
    observed_sharpe: float,
    n_obs: int,
    n_trials: int,
    returns: np.ndarray,
) -> float:
    """Bailey & López de Prado (2014) deflated Sharpe.

    Returns a p-value — the probability that the observed Sharpe is greater
    than the expected max Sharpe under the null of no edge, accounting for
    the multiple-comparison budget (``n_trials``) and the finite-sample
    skew/kurtosis of the realized returns.

    Interpretation: DSR >= 0.95 means the observed Sharpe is outside the 5%
    noise envelope expected from ``n_trials`` random variants of the same
    length. DSR close to 0.5 means the edge is indistinguishable from
    best-of-many-backtests luck.
    """
    if n_obs < 30 or n_trials < 1 or observed_sharpe <= 0.0:
        return 0.0
    # Skewness and kurtosis of the daily-PnL sample
    r = returns - np.mean(returns)
    m2 = np.mean(r**2)
    if m2 == 0.0:
        return 0.0
    m3 = np.mean(r**3)
    m4 = np.mean(r**4)
    skew = m3 / (m2**1.5) if m2 > 0 else 0.0
    kurt = m4 / (m2**2) if m2 > 0 else 3.0  # excess = kurt - 3
    # Expected max Sharpe under null (Bailey-LdP eq. 7):
    # E[max SR] ≈ ((1 - γ) * Φ⁻¹(1 - 1/N) + γ * Φ⁻¹(1 - 1/(N*e)))
    # γ = Euler-Mascheroni constant
    gamma = 0.5772156649
    from scipy.stats import norm

    phi_inv_1 = norm.ppf(1.0 - 1.0 / n_trials) if n_trials > 1 else 0.0
    phi_inv_2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    expected_max_sr = (1 - gamma) * phi_inv_1 + gamma * phi_inv_2
    # Variance of SR estimator (Mertens 2002 correction for non-normality):
    var_sr = (1 - skew * observed_sharpe + (kurt - 1) / 4 * observed_sharpe**2) / (n_obs - 1)
    if var_sr <= 0:
        return 0.0
    dsr = norm.cdf((observed_sharpe - expected_max_sr) / math.sqrt(var_sr))
    return float(dsr)


def _bootstrap_pnl_ci(
    per_trade_pnl: np.ndarray,
    iters: int,
    rng: np.random.Generator,
    ci: float = 0.95,
) -> tuple[float, float, float]:
    """Bootstrap CI on total PnL (sum across trades).

    Returns (point_estimate, lo, hi). If the CI straddles zero, the edge
    is statistically unproven at the chosen confidence level.
    """
    if per_trade_pnl.size == 0:
        return (0.0, 0.0, 0.0)
    n = per_trade_pnl.size
    totals = np.empty(iters, dtype=np.float64)
    for i in range(iters):
        idx = rng.integers(0, n, size=n)
        totals[i] = per_trade_pnl[idx].sum()
    alpha = (1.0 - ci) / 2.0
    lo = float(np.quantile(totals, alpha))
    hi = float(np.quantile(totals, 1.0 - alpha))
    return (float(per_trade_pnl.sum()), lo, hi)


# -------------------------------------------------------------------------
# Data model
# -------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VariantSummary:
    variant: str
    n_trades: int
    n_days: int
    total_pnl: float
    avg_per_trade: float
    win_rate: float
    sharpe: float
    t_stat: float
    dsr_30: float  # DSR assuming 30 variants tried
    dsr_100: float  # DSR assuming 100 variants tried
    bootstrap_lo: float
    bootstrap_hi: float
    bootstrap_ci_covers_zero: bool
    cost_sensitivity: dict[float, float]  # cost_per_trade -> surviving PnL
    quarter_pnls: list[float]
    quarter_labels: list[str]
    regime_pnls: dict[str, float]
    regime_counts: dict[str, int]


# -------------------------------------------------------------------------
# Loading + analysis
# -------------------------------------------------------------------------
def _load_trade_csv(path: Path) -> pd.DataFrame:
    """Load a trade CSV and normalize the columns we care about."""
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    # Normalize required columns; fill regime with UNKNOWN if absent
    if "regime" not in df.columns:
        df["regime"] = "UNKNOWN"
    if "pnl_dollars" not in df.columns:
        # Some CSVs only have pnl_r + multiplier; skip if no dollar column
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "pnl_dollars", "variant"])
    return df


def _quarterly_buckets(dates: pd.Series, n_buckets: int) -> list[tuple[str, pd.Series]]:
    """Split trades into ``n_buckets`` chronological equal-date ranges.

    Returns a list of (label, boolean_mask) tuples. Label format is
    "YYYY-MM-DD..YYYY-MM-DD".
    """
    if dates.empty:
        return []
    sorted_dates = dates.sort_values()
    d_min, d_max = sorted_dates.iloc[0], sorted_dates.iloc[-1]
    total_days = (d_max - d_min).days
    if total_days == 0:
        return [("single-day", dates.notna())]
    step = total_days / n_buckets
    out = []
    for i in range(n_buckets):
        lo = d_min + pd.Timedelta(days=step * i)
        hi = d_min + pd.Timedelta(days=step * (i + 1))
        mask = (dates >= lo) & (dates <= hi) if i == n_buckets - 1 else (dates >= lo) & (dates < hi)
        label = f"{lo.date()}..{hi.date()}"
        out.append((label, mask))
    return out


def _analyze_variant(
    variant: str,
    df: pd.DataFrame,
    n_buckets: int,
    bootstrap_iters: int,
    rng: np.random.Generator,
) -> VariantSummary:
    """Compute the full five-lens summary for one variant."""
    df = df.sort_values("date").reset_index(drop=True)
    per_trade = df["pnl_dollars"].to_numpy(dtype=np.float64)
    total_pnl = float(per_trade.sum())
    n_trades = int(per_trade.size)
    win_rate = float(np.mean(per_trade > 0)) if n_trades > 0 else 0.0

    # Daily aggregation for Sharpe
    daily = df.groupby("date")["pnl_dollars"].sum()
    daily_array = daily.to_numpy(dtype=np.float64)
    n_days = int(daily.size)
    sharpe = _sharpe_annualized(daily_array)
    t_stat = _t_stat(daily_array)

    # DSR under two search-budget assumptions
    dsr_30 = _deflated_sharpe(sharpe, n_days, n_trials=30, returns=daily_array)
    dsr_100 = _deflated_sharpe(sharpe, n_days, n_trials=100, returns=daily_array)

    # Bootstrap CI on total PnL
    total_est, ci_lo, ci_hi = _bootstrap_pnl_ci(per_trade, bootstrap_iters, rng)
    ci_covers_zero = ci_lo <= 0.0 <= ci_hi

    # Cost sensitivity
    cost_sensitivity: dict[float, float] = {}
    for cost in COST_SCENARIOS_PER_TRADE:
        cost_sensitivity[cost] = float(total_pnl + cost * n_trades)

    # Quarterly buckets
    buckets = _quarterly_buckets(df["date"], n_buckets)
    q_pnls: list[float] = []
    q_labels: list[str] = []
    for label, mask in buckets:
        q_pnls.append(float(df.loc[mask, "pnl_dollars"].sum()))
        q_labels.append(label)

    # Regime breakdown
    regime_pnls: dict[str, float] = {}
    regime_counts: dict[str, int] = {}
    for regime, sub in df.groupby("regime"):
        regime_pnls[str(regime)] = float(sub["pnl_dollars"].sum())
        regime_counts[str(regime)] = int(sub.shape[0])

    return VariantSummary(
        variant=variant,
        n_trades=n_trades,
        n_days=n_days,
        total_pnl=total_pnl,
        avg_per_trade=total_pnl / max(n_trades, 1),
        win_rate=win_rate,
        sharpe=sharpe,
        t_stat=t_stat,
        dsr_30=dsr_30,
        dsr_100=dsr_100,
        bootstrap_lo=ci_lo,
        bootstrap_hi=ci_hi,
        bootstrap_ci_covers_zero=ci_covers_zero,
        cost_sensitivity=cost_sensitivity,
        quarter_pnls=q_pnls,
        quarter_labels=q_labels,
        regime_pnls=regime_pnls,
        regime_counts=regime_counts,
    )


# -------------------------------------------------------------------------
# Verdict logic — same bar across every variant
# -------------------------------------------------------------------------
def _verdict(vs: VariantSummary) -> tuple[str, list[str]]:
    """Return (overall_verdict, list_of_reasons)."""
    reasons: list[str] = []
    fatal = 0
    warn = 0

    if vs.total_pnl <= 0:
        fatal += 1
        reasons.append("total PnL <= 0")
    if vs.bootstrap_ci_covers_zero:
        fatal += 1
        reasons.append("95% bootstrap CI on total PnL covers zero")
    if vs.dsr_100 < 0.5:
        fatal += 1
        reasons.append(
            f"DSR@100 trials = {vs.dsr_100:.2f} — edge indistinguishable from multi-testing noise"
        )
    elif vs.dsr_100 < 0.95:
        warn += 1
        reasons.append(f"DSR@100 trials = {vs.dsr_100:.2f} — below 0.95 significance threshold")
    if vs.cost_sensitivity.get(-5.0, 0) <= 0:
        fatal += 1
        reasons.append("PnL goes negative at $-5/trade friction")
    elif vs.cost_sensitivity.get(-1.74, 0) <= 0:
        warn += 1
        reasons.append("PnL goes negative at measured $-1.74/trade shadow parity")
    # Quarterly concentration — last bucket dead
    if len(vs.quarter_pnls) >= 4:
        last_bucket = vs.quarter_pnls[-1]
        early_buckets = sum(vs.quarter_pnls[:-1])
        if early_buckets > 0 and last_bucket <= 0:
            warn += 1
            reasons.append(
                f"edge concentrated in early buckets — last 1/{len(vs.quarter_pnls)} "
                f"of sample = ${last_bucket:+.2f}"
            )
    # Sharpe
    if vs.sharpe > 0 and vs.sharpe < 0.5:
        warn += 1
        reasons.append(f"Sharpe = {vs.sharpe:.2f} below 0.5 hurdle")

    if fatal >= 2:
        verdict = "KILL"
    elif fatal == 1:
        verdict = "FAIL"
    elif warn >= 2:
        verdict = "FRAGILE"
    elif warn == 1:
        verdict = "WATCH"
    else:
        verdict = "PASS"
    return verdict, reasons


# -------------------------------------------------------------------------
# Report rendering
# -------------------------------------------------------------------------
def _render_report(
    summaries: list[VariantSummary],
    bootstrap_iters: int,
    n_buckets: int,
) -> str:
    now = datetime.now(tz=UTC).isoformat()
    lines: list[str] = [
        f"# Edge Forensics Report — {now}",
        "",
        (
            f"Decomposition of every backtest variant that produced ≥ 1 real trade. "
            f"Five lenses: quarterly equity ({n_buckets} buckets), regime expectancy, "
            f"Deflated Sharpe (@30 and @100 multi-testing budget), {bootstrap_iters:,}-iter "
            f"bootstrap CI, and transaction-cost sensitivity at $-1.74 / $-5 / $-10 per "
            f"round trip."
        ),
        "",
        "## Verdict key",
        "",
        "- **PASS** — no fatal findings, no warnings. Edge reproducible under cost stress.",
        "- **WATCH** — one warning (e.g. Sharpe < 0.5, edge concentrated in early buckets, "
        "breakeven at measured shadow parity). Not fatal but fragile.",
        "- **FRAGILE** — two or more warnings. Ship only with tight monitoring.",
        "- **FAIL** — one fatal finding (negative PnL, CI covers zero, DSR < 0.5, "
        "PnL negative at $-5/trade).",
        "- **KILL** — two or more fatal findings. Variant is not trading material.",
        "",
        "## Headline table",
        "",
        "| Variant | Trades | Days | Total $ | /trade | Win% | Sharpe | t-stat | "
        "DSR@30 | DSR@100 | CI-lo | CI-hi | Zero? | $@-1.74 | $@-5 | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---:|---:|:---:|",
    ]
    # Sort variants by total_pnl descending
    summaries_sorted = sorted(summaries, key=lambda s: s.total_pnl, reverse=True)
    for s in summaries_sorted:
        verdict, _ = _verdict(s)
        lines.append(
            f"| `{s.variant}` | {s.n_trades} | {s.n_days} | "
            f"{s.total_pnl:+.2f} | {s.avg_per_trade:+.2f} | {s.win_rate * 100:.1f}% | "
            f"{s.sharpe:.2f} | {s.t_stat:+.2f} | {s.dsr_30:.2f} | {s.dsr_100:.2f} | "
            f"{s.bootstrap_lo:+.2f} | {s.bootstrap_hi:+.2f} | "
            f"{'YES' if s.bootstrap_ci_covers_zero else 'no'} | "
            f"{s.cost_sensitivity[-1.74]:+.2f} | {s.cost_sensitivity[-5.0]:+.2f} | "
            f"**{verdict}** |"
        )
    lines.append("")

    # Per-variant deep dive
    lines.append("## Per-variant tearsheets")
    lines.append("")
    for s in summaries_sorted:
        verdict, reasons = _verdict(s)
        lines.append(f"### `{s.variant}` — **{verdict}**")
        lines.append("")
        lines.append(
            f"- Trades: {s.n_trades} over {s.n_days} days · "
            f"total ${s.total_pnl:+.2f} · avg ${s.avg_per_trade:+.4f}/trade · "
            f"win-rate {s.win_rate * 100:.1f}%"
        )
        lines.append(
            f"- Sharpe: {s.sharpe:.2f} · t-stat: {s.t_stat:+.2f} · "
            f"DSR@30={s.dsr_30:.2f} · DSR@100={s.dsr_100:.2f}"
        )
        lines.append(
            f"- Bootstrap 95% CI on total PnL: "
            f"[${s.bootstrap_lo:+.2f}, ${s.bootstrap_hi:+.2f}] "
            f"— {'INCLUDES ZERO' if s.bootstrap_ci_covers_zero else 'excludes zero'}"
        )
        # Cost sensitivity
        lines.append("- Cost sensitivity:")
        for cost in COST_SCENARIOS_PER_TRADE:
            surv = s.cost_sensitivity[cost]
            marker = "✓" if surv > 0 else "✗"
            lines.append(f"    - @ ${cost:+.2f}/trade → ${surv:+.2f} {marker}")
        # Quarterly
        lines.append("- Quarterly decomposition:")
        for label, pnl in zip(s.quarter_labels, s.quarter_pnls, strict=False):
            pct = pnl / s.total_pnl * 100 if s.total_pnl != 0 else 0.0
            lines.append(f"    - {label}: ${pnl:+.2f} ({pct:+.1f}% of total)")
        # Regime
        if s.regime_pnls:
            lines.append("- Regime breakdown:")
            for regime, pnl in sorted(s.regime_pnls.items(), key=lambda kv: kv[1], reverse=True):
                count = s.regime_counts.get(regime, 0)
                lines.append(
                    f"    - `{regime}`: ${pnl:+.2f} over {count} trades "
                    f"(${pnl / max(count, 1):+.2f}/trade)"
                )
        # Reasons
        if reasons:
            lines.append("- Findings:")
            for r in reasons:
                lines.append(f"    - {r}")
        else:
            lines.append("- Findings: *(none — variant is clean under every lens)*")
        lines.append("")

    # Aggregate verdict
    lines.append("## Aggregate verdict")
    lines.append("")
    verdict_counts: dict[str, int] = {}
    for s in summaries:
        v, _ = _verdict(s)
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
    total = sum(verdict_counts.values())
    for v in ("PASS", "WATCH", "FRAGILE", "FAIL", "KILL"):
        n = verdict_counts.get(v, 0)
        pct = n / total * 100 if total > 0 else 0.0
        lines.append(f"- **{v}**: {n} ({pct:.1f}%)")
    lines.append("")
    lines.append(
        f"_Decomposition ran across {total} variants. "
        f"Verdict bar is intentionally harsh — a `PASS` here is a variant ready for "
        f"paper soak, not just a pretty backtest._"
    )
    lines.append("")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edge forensics decomposition.")
    parser.add_argument(
        "--buckets",
        type=int,
        default=4,
        help="Number of chronological buckets (default 4 = quarters).",
    )
    parser.add_argument(
        "--bootstrap",
        type=int,
        default=10000,
        help="Bootstrap iterations for the CI (default 10,000).",
    )
    parser.add_argument(
        "--top", type=int, default=0, help="If > 0, limit to top-N variants by trade count."
    )
    parser.add_argument(
        "--seed", type=int, default=20260418, help="Bootstrap RNG seed for reproducibility."
    )
    args = parser.parse_args(argv)

    rng = np.random.default_rng(args.seed)

    # Load every CSV we know about
    frames: list[pd.DataFrame] = []
    for csv in TRADE_CSVS:
        df = _load_trade_csv(csv)
        if not df.empty:
            frames.append(df)
    if not frames:
        print("edge_forensics: no trade CSVs found — nothing to analyze.")
        return 2
    trades = pd.concat(frames, ignore_index=True)
    variants = trades["variant"].unique().tolist()
    if args.top > 0:
        counts = trades.groupby("variant").size().sort_values(ascending=False)
        variants = counts.head(args.top).index.tolist()

    summaries: list[VariantSummary] = []
    for v in variants:
        sub = trades[trades["variant"] == v]
        if sub.shape[0] < 10:
            continue  # skip variants with negligible sample
        summary = _analyze_variant(
            variant=v,
            df=sub,
            n_buckets=args.buckets,
            bootstrap_iters=args.bootstrap,
            rng=rng,
        )
        summaries.append(summary)

    if not summaries:
        print("edge_forensics: no variants had >= 10 trades — nothing to analyze.")
        return 2

    report = _render_report(summaries, args.bootstrap, args.buckets)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(report, encoding="utf-8")

    # Machine-readable JSON sidecar — consumed by src/mnq/strategy/ship_manifest.py.
    # Each variant gets its full summary dict + verdict + reasons so the
    # manifest reader never has to re-run the expensive bootstrap.
    v_counts: dict[str, int] = {}
    json_variants: dict[str, dict] = {}
    for s in summaries:
        verdict, reasons = _verdict(s)
        v_counts[verdict] = v_counts.get(verdict, 0) + 1
        d = asdict(s)
        # cost_sensitivity keys are floats; JSON needs str keys.
        d["cost_sensitivity"] = {f"{k:.2f}": v for k, v in d["cost_sensitivity"].items()}
        d["verdict"] = verdict
        d["reasons"] = reasons
        d["shippable"] = verdict in ("PASS", "WATCH")
        json_variants[s.variant] = d

    manifest_payload = {
        "generated": datetime.now(UTC).isoformat(),
        "bootstrap_iters": int(args.bootstrap),
        "n_buckets": int(args.buckets),
        "cost_scenarios_per_trade": list(COST_SCENARIOS_PER_TRADE),
        "counts": v_counts,
        "variants": json_variants,
    }
    OUTPUT_JSON.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True))

    total = sum(v_counts.values())
    print(
        f"edge_forensics: {total} variants analyzed - "
        + " ".join(f"{k}={v}" for k, v in v_counts.items())
        + f" - report={OUTPUT.relative_to(REPO_ROOT)}"
        + f" json={OUTPUT_JSON.relative_to(REPO_ROOT)}"
    )
    any_pass = v_counts.get("PASS", 0) + v_counts.get("WATCH", 0) > 0
    return 0 if any_pass else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
