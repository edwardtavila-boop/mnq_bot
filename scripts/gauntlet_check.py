#!/usr/bin/env python3
"""Phase 3 — gauntlet-12 smoke check.

Loads the most recent 60 1-minute bars from the Databento parquet (if
available) or synthesizes a sample trajectory, constructs a
:class:`GauntletContext`, and runs all 12 gates. Writes
``reports/gauntlet.md``.

Exit: 0 always (this is a reporter). Gate vetos in live trading
happen inside the strategy's ``on_bar`` hook, not here.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mnq.gauntlet.gates.gauntlet12 import (  # noqa: E402
    GauntletContext,
    run_gauntlet,
    verdict_summary,
)

REPORT = REPO_ROOT / "reports" / "gauntlet.md"


def _synthetic_context() -> GauntletContext:
    """Synthetic uptrend with realistic noise — most gates should pass."""
    import math
    # Uptrend with realistic 1m wobble: ~5pt std over 20 bars
    closes = [
        21000.0 + i * 0.5 + 3.0 * math.sin(i * 0.7) + 2.0 * math.cos(i * 1.3)
        for i in range(60)
    ]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    volumes = [180 + (i % 5) * 12 for i in range(60)]
    # Fast/slow EMA stand-ins (last values)
    ema_fast = sum(closes[-9:]) / 9
    ema_slow = sum(closes[-21:]) / 21
    ema_fast_prev = sum(closes[-10:-1]) / 9
    ema_slow_prev = sum(closes[-22:-1]) / 21
    return GauntletContext(
        now=datetime(2026, 4, 16, 14, 30, tzinfo=UTC),
        bar_index=60,
        side="long",
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_fast_prev=ema_fast_prev,
        ema_slow_prev=ema_slow_prev,
        loss_streak=0,
        high_impact_events_minutes=[],   # clean calendar
        regime="trend_up",
        intermarket_corr=0.82,
        spread_ticks=0.5,
    )


def main() -> int:
    ctx = _synthetic_context()
    verdicts = run_gauntlet(ctx)
    summary = verdict_summary(verdicts)

    lines = [
        f"# Gauntlet-12 — {datetime.now(tz=UTC).isoformat()}",
        "",
        f"**Verdict:** {'🟢 ALLOW' if summary['allow'] else '🔴 DENY'}  ·  "
        f"{summary['passed']}/{summary['n']} passed  ·  score={summary['score']:.2f}",
        "",
        "| # | Gate | Pass | Score | Detail |",
        "|---:|---|---|---:|---|",
    ]
    for i, v in enumerate(summary["verdicts"], 1):
        mark = "🟢" if v["pass"] else "🔴"
        detail = ", ".join(f"{k}={_fmt(val)}" for k, val in v["detail"].items())
        lines.append(f"| {i} | `{v['name']}` | {mark} | {v['score']:.2f} | {detail} |")
    lines.append("")
    lines.append("Context: synthetic uptrend (stub). Wire live bars via")
    lines.append("``src/mnq/features`` to get real verdicts.")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines))

    print(
        f"gauntlet: {'🟢 ALLOW' if summary['allow'] else '🔴 DENY'} · "
        f"{summary['passed']}/{summary['n']} · score={summary['score']:.2f}"
    )
    for v in summary["verdicts"]:
        mark = "🟢" if v["pass"] else "🔴"
        print(f"  {mark} {v['name']:<18} score={v['score']:.2f}")

    # Non-blocking reporter — always 0.
    return 0


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, (list, tuple)) and len(v) <= 4:
        return json.dumps(list(v))
    return str(v)


if __name__ == "__main__":
    sys.exit(main())
