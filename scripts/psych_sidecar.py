"""Phase B #11 — Psych sidecar.

Accepts a mood/focus/energy/sleep tuple per trading day, stores it in
``data/psych_log.jsonl``, and correlates self-reported state against
trade performance.

Usage:
    python scripts/psych_sidecar.py --mood 7 --focus 8 --energy 6 --sleep 7
    python scripts/psych_sidecar.py --report
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "data" / "psych_log.jsonl"
REPORT_PATH = REPO_ROOT / "reports" / "psych_sidecar.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades  # noqa: E402


def _load_log() -> dict[str, dict]:
    if not LOG_PATH.exists():
        return {}
    out = {}
    for ln in LOG_PATH.read_text().splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln)
        out[rec["date"]] = rec
    return out


def _append_log(rec: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mood", type=int, help="1-10")
    p.add_argument("--focus", type=int, help="1-10")
    p.add_argument("--energy", type=int, help="1-10")
    p.add_argument("--sleep", type=int, help="hours")
    p.add_argument("--note", default="")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--report", action="store_true")
    args = p.parse_args()

    if not args.report:
        missing = [k for k in ("mood", "focus", "energy", "sleep") if getattr(args, k) is None]
        if missing:
            print(
                f"[psych] missing inputs: {missing}. Use --report to skip logging.", file=sys.stderr
            )
            return 2
        rec = {
            "date": args.date,
            "mood": args.mood,
            "focus": args.focus,
            "energy": args.energy,
            "sleep": args.sleep,
            "note": args.note,
            "ts": datetime.now(UTC).isoformat(),
        }
        _append_log(rec)
        print(f"psych_sidecar: logged {rec}")

    # Always write report
    log = _load_log()
    trades = load_trades()
    by_day: dict[str, list] = {}
    for t in trades:
        if t.exit_ts:
            by_day.setdefault(t.exit_ts.date().isoformat(), []).append(t)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for d in sorted(set(log) | set(by_day)):
        ts = by_day.get(d, [])
        ps = log.get(d, {})
        pnl = sum(t.net_pnl for t in ts)
        avg_r = statistics.fmean([t.r_multiple for t in ts]) if ts else 0
        rows.append(
            (
                d,
                ps.get("mood", "-"),
                ps.get("focus", "-"),
                ps.get("energy", "-"),
                ps.get("sleep", "-"),
                len(ts),
                pnl,
                avg_r,
                ps.get("note", ""),
            )
        )

    lines = [
        f"# Psych Sidecar · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- logged days: **{len(log)}** · trading days: **{len(by_day)}**",
        "",
        "## Daily ledger",
        "| Date | Mood | Focus | Energy | Sleep | N | PnL | Avg R | Note |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        d, m, f_, e, s, n, p, ar, nt = r
        lines.append(f"| {d} | {m} | {f_} | {e} | {s} | {n} | ${p:+.2f} | {ar:+.2f} | {nt[:40]} |")

    # Correlation table (mood vs PnL, etc.)
    if len(rows) >= 3:

        def _vals(idx):
            return [r for r in rows if isinstance(r[idx], int)]

        def _corr(xs, ys):
            if len(xs) < 2:
                return 0
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
            dx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
            dy = (sum((y - my) ** 2 for y in ys)) ** 0.5
            return num / (dx * dy) if dx and dy else 0

        lines += ["", "## Correlations vs daily PnL"]
        for label, idx in [("Mood", 1), ("Focus", 2), ("Energy", 3), ("Sleep", 4)]:
            vs = [(r[idx], r[6]) for r in rows if isinstance(r[idx], int)]
            if vs:
                c = _corr([x for x, _ in vs], [y for _, y in vs])
                lines.append(f"- {label}: **r = {c:+.2f}** (n={len(vs)})")

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"psych_sidecar: {len(log)} logs · {len(by_day)} trading days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
