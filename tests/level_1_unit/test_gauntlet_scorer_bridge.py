"""Tests for mnq.gauntlet.scorer_bridge — gauntlet→V3 voice conversion."""

from __future__ import annotations

from mnq.gauntlet.gates.gauntlet12 import GateVerdict
from mnq.gauntlet.scorer_bridge import (
    failed_gate_names,
    gate_pass_rate,
    gauntlet_delta,
    gauntlet_voice,
)


def _verdict(name: str, pass_: bool, score: float = 1.0) -> GateVerdict:
    return GateVerdict(name=name, pass_=pass_, score=score)


class TestGauntletVoice:
    def test_all_pass_gives_plus_100(self) -> None:
        verdicts = [_verdict(f"g{i}", True) for i in range(12)]
        assert gauntlet_voice(verdicts) == 100.0

    def test_all_fail_gives_minus_100(self) -> None:
        verdicts = [_verdict(f"g{i}", False, score=0.0) for i in range(12)]
        assert gauntlet_voice(verdicts) == -100.0

    def test_half_pass_gives_zero(self) -> None:
        verdicts = [_verdict(f"g{i}", i < 6) for i in range(12)]
        assert gauntlet_voice(verdicts) == 0.0

    def test_empty_gives_zero(self) -> None:
        assert gauntlet_voice([]) == 0.0

    def test_ten_of_twelve(self) -> None:
        verdicts = [_verdict(f"g{i}", i < 10) for i in range(12)]
        result = gauntlet_voice(verdicts)
        assert abs(result - 66.67) < 0.1

    def test_weighted_mode(self) -> None:
        verdicts = [
            _verdict("a", True, score=1.0),
            _verdict("b", True, score=0.5),
            _verdict("c", False, score=0.0),
        ]
        # avg_score = (1.0 + 0.5 + 0.0) / 3 = 0.5 → (0.5 - 0.5) * 200 = 0
        assert gauntlet_voice(verdicts, weighted=True) == 0.0

    def test_weighted_all_high(self) -> None:
        verdicts = [_verdict(f"g{i}", True, score=0.9) for i in range(4)]
        # avg = 0.9 → (0.9 - 0.5) * 200 = 80
        result = gauntlet_voice(verdicts, weighted=True)
        assert abs(result - 80.0) < 0.01


class TestGauntletDelta:
    def test_all_pass_gives_plus_one(self) -> None:
        verdicts = [_verdict(f"g{i}", True, score=1.0) for i in range(12)]
        assert gauntlet_delta(verdicts) == 1.0

    def test_all_fail_gives_minus_one(self) -> None:
        verdicts = [_verdict(f"g{i}", False, score=0.0) for i in range(12)]
        assert gauntlet_delta(verdicts) == -1.0

    def test_half_gives_zero(self) -> None:
        verdicts = [_verdict(f"g{i}", True, score=0.5) for i in range(12)]
        assert gauntlet_delta(verdicts) == 0.0

    def test_empty_gives_zero(self) -> None:
        assert gauntlet_delta([]) == 0.0


class TestGatePassRate:
    def test_all_pass(self) -> None:
        verdicts = [_verdict(f"g{i}", True) for i in range(4)]
        assert gate_pass_rate(verdicts) == 1.0

    def test_none_pass(self) -> None:
        verdicts = [_verdict(f"g{i}", False) for i in range(4)]
        assert gate_pass_rate(verdicts) == 0.0

    def test_half_pass(self) -> None:
        verdicts = [_verdict(f"g{i}", i < 2) for i in range(4)]
        assert gate_pass_rate(verdicts) == 0.5

    def test_empty(self) -> None:
        assert gate_pass_rate([]) == 0.0


class TestFailedGateNames:
    def test_no_failures(self) -> None:
        verdicts = [_verdict("a", True), _verdict("b", True)]
        assert failed_gate_names(verdicts) == []

    def test_some_failures(self) -> None:
        verdicts = [_verdict("a", True), _verdict("b", False), _verdict("c", False)]
        assert failed_gate_names(verdicts) == ["b", "c"]

    def test_all_failures(self) -> None:
        verdicts = [_verdict("x", False), _verdict("y", False)]
        assert failed_gate_names(verdicts) == ["x", "y"]

    def test_empty(self) -> None:
        assert failed_gate_names([]) == []
