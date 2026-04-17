"""[REAL] Bar sequence validation.

`Bar.__post_init__` enforces per-bar OHLC invariants. This module adds
*sequence*-level invariants: strict monotonic timestamps, uniform cadence
within a run (modulo the expected session gap), no duplicates, and no
out-of-order inserts.

Every ingestion path — historical replay, WS re-subscribe after a
reconnect, fixture loaders — should call `validate_bar_sequence` before
feeding bars to a simulator or executor. Silently-mis-ordered bars are
the #1 source of backtest↔live divergence and are otherwise impossible
to detect once features have rolled forward.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from mnq.core.types import Bar


class BarSequenceError(ValueError):
    """A bar sequence violates a structural invariant."""


@dataclass(frozen=True)
class BarSequenceReport:
    """Non-fatal summary useful for monitoring. A report with `ok=False`
    indicates the caller should treat the sequence as corrupt."""

    ok: bool
    n_bars: int
    duplicates: int
    backwards: int
    gaps: int
    anomalies: list[str]


def validate_bar_sequence(
    bars: Sequence[Bar],
    *,
    strict: bool = True,
    allow_gaps: bool = True,
    max_gap_multiple: float = 60.0,
) -> BarSequenceReport:
    """Check sequence-level invariants over an ordered run of Bar objects.

    Args:
        bars: Sequence of `Bar` to validate. Must share a single timeframe.
        strict: If True, raise `BarSequenceError` on the first violation.
            If False, collect all anomalies into the returned report.
        allow_gaps: If False, every consecutive pair must be exactly
            `timeframe_sec` apart. If True, we tolerate gaps up to
            `max_gap_multiple × timeframe_sec` (session breaks, holidays,
            maintenance) but still flag unexpectedly large jumps.
        max_gap_multiple: Upper bound on tolerated gap in units of
            `timeframe_sec`. Default 60x — conservative for a 1m bar
            crossing a weekend that includes Monday CME maintenance.

    Returns:
        BarSequenceReport. `.ok` is False iff any anomaly was found.
    """
    n = len(bars)
    if n == 0:
        return BarSequenceReport(True, 0, 0, 0, 0, [])

    tf = bars[0].timeframe_sec
    expected = timedelta(seconds=tf)
    max_allowed = expected * max_gap_multiple

    anomalies: list[str] = []
    dups = 0
    back = 0
    gaps = 0

    prev = bars[0]
    if prev.timeframe_sec != tf:
        anomalies.append(f"bars[0].timeframe_sec={prev.timeframe_sec} != expected {tf}")

    for i in range(1, n):
        cur = bars[i]
        if cur.timeframe_sec != tf:
            msg = f"bars[{i}].timeframe_sec={cur.timeframe_sec} != {tf}"
            if strict:
                raise BarSequenceError(msg)
            anomalies.append(msg)

        delta = cur.ts - prev.ts
        if delta == timedelta(0):
            dups += 1
            msg = f"bars[{i}] duplicate timestamp {cur.ts.isoformat()}"
            if strict:
                raise BarSequenceError(msg)
            anomalies.append(msg)
        elif delta < timedelta(0):
            back += 1
            msg = (
                f"bars[{i}] ts={cur.ts.isoformat()} is before "
                f"bars[{i - 1}] ts={prev.ts.isoformat()}"
            )
            if strict:
                raise BarSequenceError(msg)
            anomalies.append(msg)
        elif delta > expected:
            # gap vs allowed
            if not allow_gaps:
                msg = f"bars[{i}] unexpected gap: delta={delta}, expected {expected}"
                if strict:
                    raise BarSequenceError(msg)
                anomalies.append(msg)
                gaps += 1
            elif delta > max_allowed:
                gaps += 1
                msg = f"bars[{i}] oversized gap: delta={delta} > max_allowed={max_allowed}"
                if strict:
                    raise BarSequenceError(msg)
                anomalies.append(msg)

        prev = cur

    return BarSequenceReport(
        ok=not anomalies,
        n_bars=n,
        duplicates=dups,
        backwards=back,
        gaps=gaps,
        anomalies=anomalies,
    )


def dedupe_sorted(bars: Sequence[Bar]) -> list[Bar]:
    """Return a copy of `bars` with exact-timestamp duplicates removed.

    Last-writer-wins on duplicate timestamps (matches WS re-subscribe
    semantics: the newer bar carries the completed OHLCV whereas the
    earlier one may have been an in-progress snapshot). Assumes input
    is monotonically non-decreasing in `ts`.
    """
    if not bars:
        return []
    out: list[Bar] = [bars[0]]
    for b in bars[1:]:
        if b.ts == out[-1].ts:
            out[-1] = b
        else:
            out.append(b)
    return out
