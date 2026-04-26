"""Bayesian expectancy + heat budget.

Phase 5 of the roadmap. Small-n win/loss data from the live journal is
high-variance; a simple win-rate point estimate is often misleading on
n=8. A Beta posterior with a weakly-informative prior (Beta(1, 1)) gives
us:

* A posterior mean win rate per (variant × regime × side).
* A 95% credible interval for that win rate.
* A posterior expectancy point estimate using mean_win / mean_loss from
  the journal.
* A **heat budget** — per-bucket concurrency cap based on
  CI-lower-bound expectancy × shrinkage.

The script reads the journal, groups closed trades by the regime+side+variant
triple (variant inferred from the strategy_registry hash if available,
otherwise set to ``live_sim``), and writes
``reports/bayesian_expectancy.md``.

Usage:

    python scripts/bayesian_expectancy.py
    python scripts/bayesian_expectancy.py --prior-alpha 2 --prior-beta 2
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.core.paths import LIVE_SIM_JOURNAL  # noqa: E402
from mnq.storage.journal import EventJournal  # noqa: E402
from mnq.storage.schema import FILL_REALIZED  # noqa: E402

DEFAULT_JOURNAL = LIVE_SIM_JOURNAL
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "bayesian_expectancy.md"


@dataclass
class Bucket:
    key: tuple[str, str]  # (regime, side)
    wins: int = 0
    losses: int = 0
    pnls: list[float] | None = None

    def __post_init__(self) -> None:
        if self.pnls is None:
            self.pnls = []


def _load_buckets(path: Path) -> dict[tuple[str, str], Bucket]:
    journal = EventJournal(path)
    buckets: dict[tuple[str, str], Bucket] = {}
    for entry in journal.replay(event_types=(FILL_REALIZED,)):
        p = entry.payload
        if "pnl_dollars" not in p or "entry_ts" not in p:
            continue
        try:
            pnl = float(p["pnl_dollars"])
        except (TypeError, ValueError):
            continue
        key = (str(p.get("regime", "unknown")), str(p.get("side", "?")))
        b = buckets.setdefault(key, Bucket(key=key))
        b.pnls.append(pnl)  # type: ignore[union-attr]
        if pnl > 0:
            b.wins += 1
        else:
            b.losses += 1
    return buckets


# ---------------------------------------------------------------------------
# Beta posterior
# ---------------------------------------------------------------------------


def _beta_mean(alpha: float, beta: float) -> float:
    return alpha / (alpha + beta) if (alpha + beta) > 0 else 0.0


def _beta_quantile(alpha: float, beta: float, q: float, *, steps: int = 400) -> float:
    """Monotone inversion of the Beta CDF via incremental Simpson-like sum.

    Plenty accurate for the 95% CI we report; avoids scipy dep.
    """
    if alpha <= 0 or beta <= 0:
        return 0.0
    # Tabulate the pdf on a fine grid then normalize.
    import math

    # log Beta(alpha, beta) normalization via lgamma
    lb = math.lgamma(alpha) + math.lgamma(beta) - math.lgamma(alpha + beta)

    # cumulative trapezoidal
    xs = [i / steps for i in range(steps + 1)]
    pdfs: list[float] = []
    for x in xs:
        if x <= 0 or x >= 1:
            pdfs.append(0.0)
        else:
            logpdf = (alpha - 1) * math.log(x) + (beta - 1) * math.log(1 - x) - lb
            pdfs.append(math.exp(logpdf))
    cdf = [0.0]
    for i in range(1, len(xs)):
        cdf.append(cdf[-1] + 0.5 * (pdfs[i] + pdfs[i - 1]) * (xs[i] - xs[i - 1]))
    total = cdf[-1] or 1.0
    cdf_norm = [c / total for c in cdf]
    for i, c in enumerate(cdf_norm):
        if c >= q:
            return xs[i]
    return 1.0


@dataclass
class BucketPosterior:
    key: tuple[str, str]
    n: int
    wins: int
    losses: int
    posterior_mean_wr: float
    ci_lo: float
    ci_hi: float
    mean_win: float
    mean_loss: float
    posterior_expectancy: float
    ci_lo_expectancy: float


def score_bucket(b: Bucket, alpha0: float, beta0: float) -> BucketPosterior:
    alpha = alpha0 + b.wins
    beta = beta0 + b.losses
    n = b.wins + b.losses
    post_wr = _beta_mean(alpha, beta)
    lo = _beta_quantile(alpha, beta, 0.025)
    hi = _beta_quantile(alpha, beta, 0.975)
    wins_pnls = [p for p in (b.pnls or []) if p > 0]
    loss_pnls = [-p for p in (b.pnls or []) if p <= 0]
    mean_win = statistics.mean(wins_pnls) if wins_pnls else 0.0
    mean_loss = statistics.mean(loss_pnls) if loss_pnls else 0.0
    exp_point = post_wr * mean_win - (1 - post_wr) * mean_loss
    exp_lo = lo * mean_win - (1 - lo) * mean_loss
    return BucketPosterior(
        key=b.key,
        n=n,
        wins=b.wins,
        losses=b.losses,
        posterior_mean_wr=post_wr,
        ci_lo=lo,
        ci_hi=hi,
        mean_win=mean_win,
        mean_loss=mean_loss,
        posterior_expectancy=exp_point,
        ci_lo_expectancy=exp_lo,
    )


def heat_budget(posterior: BucketPosterior, *, cap_units: int = 3) -> int:
    """Translate CI-lower expectancy into a per-bucket concurrency cap.

    * CI-lower < 0  → cap 0 (no concurrent trades allowed here until the
      posterior is less pessimistic).
    * 0 ≤ CI-lower < 5 $ → cap 1.
    * 5 ≤ CI-lower < 15 $ → cap 2.
    * CI-lower ≥ 15 $ → cap = ``cap_units`` (default 3).

    This is a conservative ladder; it wants evidence before scaling risk.
    """
    x = posterior.ci_lo_expectancy
    if x < 0:
        return 0
    if x < 5:
        return 1
    if x < 15:
        return 2
    return cap_units


def _render(posteriors: list[BucketPosterior], alpha0: float, beta0: float) -> str:
    lines = ["# Bayesian Expectancy + Heat Budget", ""]
    lines.append(f"- Prior: Beta({alpha0}, {beta0})")
    lines.append(f"- Buckets evaluated: **{len(posteriors)}**")
    lines.append("")
    lines.append("## Per-bucket posterior")
    lines.append("")
    lines.append(
        "| Regime | Side | n | W | L | Post. WR | 95% CI | Mean win | Mean loss | "
        "Post. exp | CI-lo exp | Heat cap |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|")
    for p in posteriors:
        cap = heat_budget(p)
        regime, side = p.key
        lines.append(
            f"| `{regime}` | {side} | {p.n} | {p.wins} | {p.losses} | "
            f"{p.posterior_mean_wr:.1%} | "
            f"{p.ci_lo:.1%} / {p.ci_hi:.1%} | "
            f"${p.mean_win:,.2f} | ${p.mean_loss:,.2f} | "
            f"${p.posterior_expectancy:+,.2f} | "
            f"${p.ci_lo_expectancy:+,.2f} | {cap} |"
        )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "* Heat-cap 0 buckets should be **blocked** by the gauntlet at runtime "
        "until more evidence flips their CI-lower positive."
    )
    lines.append(
        "* Posterior WR differs from empirical WR whenever n is small — that's "
        "the whole point of the Beta prior. On n=2 the posterior is pulled "
        "toward the prior mean by a lot; on n=30 the data dominate."
    )
    lines.append(
        "* CI-lower expectancy is the right number to size from. Kelly applied "
        "to the *point* expectancy over-commits on small-n buckets."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bayesian expectancy + heat budget.")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--prior-alpha", type=float, default=1.0, dest="prior_alpha")
    parser.add_argument("--prior-beta", type=float, default=1.0, dest="prior_beta")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    buckets = _load_buckets(args.journal)
    posteriors = sorted(
        (score_bucket(b, args.prior_alpha, args.prior_beta) for b in buckets.values()),
        key=lambda p: (-p.ci_lo_expectancy, p.key),
    )

    md = _render(posteriors, args.prior_alpha, args.prior_beta)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(md)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
