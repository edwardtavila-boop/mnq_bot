"""The 12-gate filter gauntlet.

Each gate is a pure function ``(ctx: GauntletContext) -> GateVerdict``
that consumes recent bars + intermarket + event calendar + regime
classification and returns a verdict with a structured ``detail`` dict.

The 12 gates are (ordered cheap → expensive):

1. ``session``       — RTH window + lunch-lull carve-out
2. ``time_of_day``   — hour-of-day expectancy bucket (green/yellow/red)
3. ``vol_band``      — ATR / stdev inside [min, max] band
4. ``trend_align``   — fast EMA vs slow EMA slope confirms direction
5. ``cross_mag``     — EMA cross magnitude ≥ threshold (filters noise)
6. ``orderflow``     — crude CVD or bar-body proxy confirms pressure
7. ``volume_confirm``— current-bar volume ≥ N-bar SMA × factor
8. ``streak``        — cap consecutive losses before trading resumes
9. ``news_window``   — no HIGH-impact event within ±N minutes
10. ``regime``        — regime classifier agrees with trade direction
11. ``correlation``   — intermarket (SPX/ES) not diverging sharply
12. ``spread``        — synthetic bid-ask spread ≤ tolerance

Each gate is intentionally self-contained and returns its own
numeric score in ``detail["score"]`` so the 15-voice engine in
``eta_v3_framework`` can aggregate them later.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time


@dataclass(frozen=True, slots=True)
class GateVerdict:
    name: str
    pass_: bool
    score: float          # 0.0–1.0 confidence
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class GauntletContext:
    """Snapshot of the signals needed to evaluate all 12 gates.

    Everything is optional — absent fields cause the relevant gate to
    return PASS with a ``stub`` note. This lets the gauntlet run on
    partial data (e.g. no live CVD feed) without blocking the whole
    chain.
    """
    now: datetime
    bar_index: int = 0                # 0-based index within day
    side: str = "long"                # "long" | "short"
    closes: list[float] = field(default_factory=list)   # recent 1m closes
    highs:  list[float] = field(default_factory=list)
    lows:   list[float] = field(default_factory=list)
    volumes: list[int]  = field(default_factory=list)
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_fast_prev: float | None = None
    ema_slow_prev: float | None = None
    loss_streak: int = 0
    high_impact_events_minutes: list[int] = field(default_factory=list)
    regime: str | None = None          # "trend_up" | "trend_down" | "chop" | ...
    intermarket_corr: float | None = None   # e.g. ES correlation
    spread_ticks: float | None = None
    # Order flow features (Batch 7A — from orderflow.py)
    cvd: float | None = None               # cumulative volume delta
    bar_delta: float | None = None          # single-bar volume delta
    imbalance: float | None = None          # bid/ask imbalance [-1, +1]
    absorption_score: float | None = None   # range/body proxy [0, 1]
    buy_aggressor_pct: float | None = None  # buyer aggression [0, 1]
    # Intermarket correlation data (Batch 7B)
    es_closes: list[float] = field(default_factory=list)  # ES 1m closes (parallel to MNQ)


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------

RTH_START = time(13, 30)      # 09:30 ET → 13:30 UTC
RTH_END   = time(20, 0)       # 16:00 ET → 20:00 UTC
LUNCH_START = time(16, 0)
LUNCH_END   = time(17, 30)


def gate_session(ctx: GauntletContext) -> GateVerdict:
    t = ctx.now.astimezone(UTC).time()
    in_rth = RTH_START <= t <= RTH_END
    in_lunch = LUNCH_START <= t <= LUNCH_END
    ok = in_rth and not in_lunch
    return GateVerdict("session", ok, 1.0 if ok else 0.0, {"time": t.isoformat(), "in_lunch": in_lunch})


# Rough expectancy bucket from time_heatmap learnings. Green = best.
_HOUR_BUCKETS = {
    13: "yellow", 14: "green", 15: "green",   # 9-11 ET
    16: "yellow", 17: "red",                  # lunch transition
    18: "green", 19: "green", 20: "yellow",   # 2-4 ET
}


def gate_time_of_day(ctx: GauntletContext) -> GateVerdict:
    hour = ctx.now.astimezone(UTC).hour
    bucket = _HOUR_BUCKETS.get(hour, "yellow")
    ok = bucket != "red"
    score = {"green": 1.0, "yellow": 0.6, "red": 0.0}[bucket]
    return GateVerdict("time_of_day", ok, score, {"hour": hour, "bucket": bucket})


def _stdev(xs: Iterable[float]) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return var ** 0.5


def gate_vol_band(ctx: GauntletContext, *, band: tuple[float, float] = (3.0, 40.0)) -> GateVerdict:
    if len(ctx.closes) < 20:
        return GateVerdict("vol_band", True, 0.5, {"stub": "insufficient bars"})
    sd = _stdev(ctx.closes[-20:])
    ok = band[0] <= sd <= band[1]
    mid = (band[0] + band[1]) / 2
    score = 1.0 - min(1.0, abs(sd - mid) / mid)
    return GateVerdict("vol_band", ok, max(0.0, score), {"stdev": sd, "band": band})


def gate_trend_align(ctx: GauntletContext) -> GateVerdict:
    if None in (ctx.ema_fast, ctx.ema_slow, ctx.ema_fast_prev, ctx.ema_slow_prev):
        return GateVerdict("trend_align", True, 0.5, {"stub": "ema series missing"})
    fast, slow = ctx.ema_fast, ctx.ema_slow
    fast_p, slow_p = ctx.ema_fast_prev, ctx.ema_slow_prev
    d_fast = fast - fast_p
    d_slow = slow - slow_p
    if ctx.side == "long":
        ok = d_fast > 0 and d_slow >= 0 and fast > slow
    else:
        ok = d_fast < 0 and d_slow <= 0 and fast < slow
    score = 1.0 if ok else 0.0
    return GateVerdict("trend_align", ok, score,
                       {"d_fast": d_fast, "d_slow": d_slow, "fast": fast, "slow": slow})


def gate_cross_mag(ctx: GauntletContext, *, min_mag: float = 1.5) -> GateVerdict:
    if ctx.ema_fast is None or ctx.ema_slow is None:
        return GateVerdict("cross_mag", True, 0.5, {"stub": "ema missing"})
    mag = abs(ctx.ema_fast - ctx.ema_slow)
    ok = mag >= min_mag
    return GateVerdict("cross_mag", ok, min(1.0, mag / (min_mag * 2)), {"mag": mag, "min": min_mag})


def gate_orderflow(ctx: GauntletContext) -> GateVerdict:
    """Order flow confirmation — uses real CVD/imbalance when available.

    Batch 7A. Three-tier logic:
      1. If CVD + imbalance are populated (from OrderFlowTracker), use them.
      2. If only CVD is populated, use CVD alone.
      3. Fallback: crude body × volume proxy (original Batch 5A logic).

    Scoring:
      - CVD direction confirms trade → 0.5 base
      - Imbalance confirms trade → +0.25
      - Absorption detected (doji at level) → +0.15
      - Buy aggressor pct aligns → +0.10
    """
    detail: dict = {}

    # Tier 1: real order flow features available
    if ctx.cvd is not None and ctx.imbalance is not None:
        wants_positive = ctx.side == "long"

        # CVD confirmation (0.5 weight)
        cvd_ok = (ctx.cvd > 0) if wants_positive else (ctx.cvd < 0)
        cvd_score = 0.5 if cvd_ok else 0.0

        # Imbalance confirmation (0.25 weight)
        imb_ok = (ctx.imbalance > 0.1) if wants_positive else (ctx.imbalance < -0.1)
        imb_score = 0.25 if imb_ok else 0.0

        # Absorption bonus (0.15 weight)
        abs_score = 0.15 if (ctx.absorption_score or 0) > 0.6 else 0.0

        # Aggressor alignment (0.10 weight)
        agg = ctx.buy_aggressor_pct
        agg_ok = (agg is not None and agg > 0.6) if wants_positive else (agg is not None and agg < 0.4)
        agg_score = 0.10 if agg_ok else 0.0

        score = cvd_score + imb_score + abs_score + agg_score
        ok = score >= 0.5
        detail = {
            "mode": "orderflow_tracker", "cvd": ctx.cvd,
            "imbalance": ctx.imbalance, "absorption": ctx.absorption_score,
            "buy_agg": ctx.buy_aggressor_pct, "cvd_ok": cvd_ok, "imb_ok": imb_ok,
        }
        return GateVerdict("orderflow", ok, score, detail)

    # Tier 2: CVD only
    if ctx.cvd is not None:
        wants_positive = ctx.side == "long"
        ok = (ctx.cvd > 0) if wants_positive else (ctx.cvd < 0)
        score = 0.7 if ok else 0.2
        return GateVerdict("orderflow", ok, score, {"mode": "cvd_only", "cvd": ctx.cvd})

    # Tier 3: fallback — crude body × volume proxy
    if len(ctx.closes) < 5 or len(ctx.volumes) < 5:
        return GateVerdict("orderflow", True, 0.5, {"mode": "stub", "reason": "insufficient bars"})
    body = 0.0
    for i in range(-5, 0):
        c = ctx.closes[i]
        o = ctx.closes[i - 1] if len(ctx.closes) + i - 1 >= 0 else c
        body += (c - o) * ctx.volumes[i]
    ok = body > 0 if ctx.side == "long" else body < 0
    score = 1.0 if ok else 0.0
    return GateVerdict("orderflow", ok, score, {"mode": "proxy", "net_body_vol": body})


def gate_volume_confirm(ctx: GauntletContext, *, factor: float = 1.0) -> GateVerdict:
    if len(ctx.volumes) < 21:
        return GateVerdict("volume_confirm", True, 0.5, {"stub": "insufficient bars"})
    sma = sum(ctx.volumes[-21:-1]) / 20
    cur = ctx.volumes[-1]
    ok = cur >= sma * factor
    score = min(1.0, cur / (sma * factor)) if sma > 0 else 0.5
    return GateVerdict("volume_confirm", ok, score, {"cur": cur, "sma20": sma})


def gate_streak(ctx: GauntletContext, *, max_streak: int = 3) -> GateVerdict:
    ok = ctx.loss_streak < max_streak
    return GateVerdict("streak", ok, 1.0 - (ctx.loss_streak / max_streak),
                       {"loss_streak": ctx.loss_streak, "max": max_streak})


def gate_news_window(ctx: GauntletContext, *, window_min: int = 30) -> GateVerdict:
    if not ctx.high_impact_events_minutes:
        return GateVerdict("news_window", True, 1.0, {"events": 0})
    nearest = min(abs(m) for m in ctx.high_impact_events_minutes)
    ok = nearest > window_min
    score = min(1.0, nearest / (window_min * 4))
    return GateVerdict("news_window", ok, score, {"nearest_min": nearest, "window": window_min})


def gate_regime(ctx: GauntletContext) -> GateVerdict:
    if ctx.regime is None:
        return GateVerdict("regime", True, 0.5, {"stub": "no classifier"})
    wants_up = ctx.side == "long"
    ok = ctx.regime in ("trend_up",) if wants_up else ctx.regime in ("trend_down",)
    # chop penalized but not forbidden; high_vol / range hard-block
    if ok:
        score = 1.0
    elif ctx.regime == "chop":
        score = 0.3
    elif ctx.regime in ("high_vol", "range"):
        # high-vol and tight-range days are too uncertain for automated
        # entries. Keep them hard-blocked (score 0.0).
        score = 0.0
    else:
        score = 0.0
    return GateVerdict("regime", score > 0.0, score, {"regime": ctx.regime, "side": ctx.side})


def gate_correlation(ctx: GauntletContext, *, min_corr: float = 0.0) -> GateVerdict:
    """Intermarket correlation — MNQ vs ES/SPX.

    Batch 7B. Three modes:
      1. Pre-computed ``intermarket_corr`` (injected externally) — use as-is.
      2. ``es_closes`` provided → compute rolling 20-bar Pearson correlation.
      3. Neither → pass with stub.

    Divergence (low/negative correlation) warns that MNQ is moving
    independently of the broader market — higher risk of reversal.
    """
    # Mode 1: pre-computed
    if ctx.intermarket_corr is not None:
        ok = ctx.intermarket_corr >= min_corr
        score = max(0.0, min(1.0, ctx.intermarket_corr))
        return GateVerdict("correlation", ok, score,
                           {"mode": "precomputed", "corr": ctx.intermarket_corr})

    # Mode 2: compute from ES closes
    if ctx.es_closes and len(ctx.es_closes) >= 20 and len(ctx.closes) >= 20:
        n = min(len(ctx.closes), len(ctx.es_closes), 20)
        mnq = ctx.closes[-n:]
        es = ctx.es_closes[-n:]
        # Pearson correlation
        m_mnq = sum(mnq) / n
        m_es = sum(es) / n
        cov = sum((a - m_mnq) * (b - m_es) for a, b in zip(mnq, es, strict=True)) / n
        std_mnq = (sum((a - m_mnq) ** 2 for a in mnq) / n) ** 0.5
        std_es = (sum((b - m_es) ** 2 for b in es) / n) ** 0.5
        corr = cov / (std_mnq * std_es) if std_mnq > 0 and std_es > 0 else 0.0
        ok = corr >= min_corr
        score = max(0.0, min(1.0, corr))
        return GateVerdict("correlation", ok, score,
                           {"mode": "computed", "corr": round(corr, 4), "n_bars": n})

    # Mode 3: stub
    return GateVerdict("correlation", True, 0.5, {"mode": "stub", "reason": "no intermarket"})


def gate_spread(ctx: GauntletContext, *, max_ticks: float = 2.0) -> GateVerdict:
    if ctx.spread_ticks is None:
        return GateVerdict("spread", True, 1.0, {"stub": "no spread feed"})
    ok = ctx.spread_ticks <= max_ticks
    score = max(0.0, 1.0 - ctx.spread_ticks / (max_ticks * 2))
    return GateVerdict("spread", ok, score, {"spread_ticks": ctx.spread_ticks, "max": max_ticks})


# Canonical gauntlet — the exact 12.
GAUNTLET: tuple = (
    gate_session,
    gate_time_of_day,
    gate_vol_band,
    gate_trend_align,
    gate_cross_mag,
    gate_orderflow,
    gate_volume_confirm,
    gate_streak,
    gate_news_window,
    gate_regime,
    gate_correlation,
    gate_spread,
)


def run_gauntlet(ctx: GauntletContext) -> list[GateVerdict]:
    """Evaluate all 12 gates against ``ctx`` and return the verdicts."""
    return [g(ctx) for g in GAUNTLET]


def verdict_summary(verdicts: list[GateVerdict]) -> dict[str, object]:
    """Aggregate gauntlet output into a single summary dict."""
    n = len(verdicts)
    passed = sum(1 for v in verdicts if v.pass_)
    score = sum(v.score for v in verdicts) / n if n else 0.0
    return {
        "n": n,
        "passed": passed,
        "failed": n - passed,
        "score": score,
        "allow": all(v.pass_ for v in verdicts),
        "verdicts": [
            {"name": v.name, "pass": v.pass_, "score": v.score, "detail": v.detail}
            for v in verdicts
        ],
    }
