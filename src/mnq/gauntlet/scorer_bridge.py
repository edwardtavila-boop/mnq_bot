"""Bridge gauntlet gate scores into a V3-compatible voice score.

Batch 5C. Converts the 12 gauntlet gate verdicts into a single
``gauntlet_voice`` score in the range [-100, +100] that the V3 engine
can consume as an additional voice (V16).

The score is computed as:

    raw = (pass_rate - 0.5) * 2 * 100

Where ``pass_rate`` is the fraction of gates that passed (0.0-1.0).

  * All 12 pass → +100 (strong go)
  * 10/12 pass → +66.7 (moderate go)
  * 6/12 pass → 0 (neutral)
  * 3/12 pass → -50 (moderate stop)
  * 0/12 pass → -100 (full stop)

An optional weighted mode uses each gate's ``score`` field for a
more granular signal.

Usage:

    from mnq.gauntlet.scorer_bridge import gauntlet_voice
    from mnq.gauntlet.gates.gauntlet12 import run_gauntlet

    verdicts = run_gauntlet(ctx)
    voice_score = gauntlet_voice(verdicts)
    # voice_score is in [-100, +100], ready for V3 engine injection
"""
from __future__ import annotations

from mnq.gauntlet.gates.gauntlet12 import GateVerdict


def gauntlet_voice(verdicts: list[GateVerdict], *, weighted: bool = False) -> float:
    """Convert gauntlet verdicts into a V3-compatible voice score.

    Args:
        verdicts: List of GateVerdict objects from ``run_gauntlet()``.
        weighted: If True, use individual gate scores (0.0-1.0) instead
            of binary pass/fail. Produces a smoother signal.

    Returns:
        Score in [-100, +100]. Zero = neutral (50% pass rate).
    """
    if not verdicts:
        return 0.0

    if weighted:
        avg_score = sum(v.score for v in verdicts) / len(verdicts)
    else:
        avg_score = sum(1.0 for v in verdicts if v.pass_) / len(verdicts)

    # Map [0, 1] → [-100, +100] with 0.5 as neutral
    return (avg_score - 0.5) * 2.0 * 100.0


def gauntlet_delta(verdicts: list[GateVerdict]) -> float:
    """Compute a gauntlet delta analogous to the Apex V3 delta.

    Returns a value in [-1.0, +1.0]:
      * +1.0 = all gates passed with max score
      * 0.0 = 50% pass rate
      * -1.0 = all gates failed

    This can be used alongside the Apex delta for a composite
    confidence signal.
    """
    if not verdicts:
        return 0.0
    avg_score = sum(v.score for v in verdicts) / len(verdicts)
    return (avg_score - 0.5) * 2.0


def gate_pass_rate(verdicts: list[GateVerdict]) -> float:
    """Fraction of gates that passed (0.0-1.0)."""
    if not verdicts:
        return 0.0
    return sum(1.0 for v in verdicts if v.pass_) / len(verdicts)


def failed_gate_names(verdicts: list[GateVerdict]) -> list[str]:
    """Names of gates that failed."""
    return [v.name for v in verdicts if not v.pass_]
