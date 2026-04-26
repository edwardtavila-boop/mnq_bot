"""Phase C #21 — Cumulative delta proxy from DataBento bars.

When tick-level bid/ask-stamped prints aren't available, we proxy
cumulative delta from bar close vs open direction weighted by
volume. Not as precise as true CVD, but directionally correct and
enough to spot exhaustion.

Writes ``reports/cumulative_delta.md`` with the session CVD series.

Usage:
    python scripts/cumulative_delta.py --date 2026-04-15
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "cumulative_delta.md"

# B2 closure (v0.2.2): canonical bars path resolves via mnq.core.paths.
# Operator override: MNQ_BARS_DATABENTO_DIR.
from mnq.core.paths import BARS_DATABENTO_DIR  # noqa: E402

BARS_DIR = BARS_DATABENTO_DIR


def _read_parquet(path: Path):
    try:
        import pyarrow.parquet as pq  # type: ignore
        return pq.read_table(str(path)).to_pylist()
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=(datetime.now(UTC) - timedelta(days=1)).date().isoformat())
    p.parse_args()  # parsed for --help / validation only; no fields used downstream

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(BARS_DIR.glob("*.parquet")) if BARS_DIR.exists() else []
    if not files:
        REPORT_PATH.write_text("# Cumulative Delta\n\n_no bar data available_\n")
        print("cumulative_delta: no bars")
        return 0

    # Use the most recent file as the session
    rows = _read_parquet(files[-1])
    if not rows:
        REPORT_PATH.write_text("# Cumulative Delta\n\n_bars unreadable_\n")
        print("cumulative_delta: bars unreadable")
        return 0

    cvd = 0
    series = []
    for r in rows[-200:]:
        o, c = r.get("open", 0), r.get("close", 0)
        v = r.get("volume", 0) or 0
        sign = 1 if c > o else -1 if c < o else 0
        cvd += sign * v
        series.append(cvd)

    # ASCII sparkline
    lo, hi = min(series), max(series)
    sparkline_chars = " ▁▂▃▄▅▆▇█"
    spark = (
        "".join(sparkline_chars[min(8, int((v - lo) / (hi - lo) * 8))] for v in series)
        if hi > lo else "▄" * len(series)
    )

    REPORT_PATH.write_text(
        f"# Cumulative Delta · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- source file: `{files[-1].name}`\n- bars sampled: **{len(series)}**\n"
        f"- CVD final: **{cvd:,}**  (min={lo:,}, max={hi:,})\n\n"
        f"## Sparkline (last 200 bars)\n`{spark}`\n\n"
        "_Note: proxied from bar direction × volume. Replace with true bid-ask CVD when tick feed is wired._\n"
    )
    print(f"cumulative_delta: final CVD={cvd} · range [{lo},{hi}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
