"""Unit tests for the pre-trade gate chain."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnq.risk import GateChain, GateResult, build_default_chain
from mnq.risk.gate_chain import (
    correlation_gate,
    deadman_gate,
    governor_gate,
    heartbeat_gate,
    pre_trade_pause_gate,
)


def _mk_gate(name: str, allow: bool, reason: str = "test"):
    def _g() -> GateResult:
        return GateResult(allow, name, reason)
    _g.name = name  # type: ignore[attr-defined]
    return _g


class TestGateChain:
    def test_all_allow_passes(self):
        chain = GateChain(gates=(_mk_gate("a", True), _mk_gate("b", True)))
        ok, results = chain.evaluate()
        assert ok is True
        assert len(results) == 2
        assert all(r.allow for r in results)

    def test_short_circuit_on_first_deny(self):
        chain = GateChain(
            gates=(_mk_gate("a", True), _mk_gate("b", False, "NO"), _mk_gate("c", True))
        )
        ok, results = chain.evaluate()
        assert ok is False
        assert len(results) == 2  # gate c never evaluated
        assert results[-1].reason == "NO"

    def test_empty_chain_allows(self):
        chain = GateChain(gates=())
        ok, results = chain.evaluate()
        assert ok is True
        assert results == []

    def test_summary_runs_every_gate(self):
        chain = GateChain(gates=(_mk_gate("a", True), _mk_gate("b", False), _mk_gate("c", True)))
        snap = chain.summary()
        assert snap["allow_all"] is False
        assert len(snap["gates"]) == 3


class TestHeartbeatGate:
    def test_missing_state_fails_open(self, tmp_path: Path):
        r = heartbeat_gate(path=tmp_path / "nope.json")
        assert r.allow is True
        assert r.reason == "no-state"

    def test_fresh_heartbeat_allows(self, tmp_path: Path):
        p = tmp_path / "hb.json"
        p.write_text(json.dumps({"ts": datetime.now(tz=UTC).isoformat()}))
        r = heartbeat_gate(path=p, max_age_sec=300)
        assert r.allow is True
        assert r.reason == "alive"

    def test_stale_heartbeat_denies(self, tmp_path: Path):
        p = tmp_path / "hb.json"
        old = datetime.now(tz=UTC) - timedelta(seconds=1000)
        p.write_text(json.dumps({"ts": old.isoformat()}))
        r = heartbeat_gate(path=p, max_age_sec=300)
        assert r.allow is False
        assert "stale" in r.reason

    def test_malformed_ts_denies(self, tmp_path: Path):
        p = tmp_path / "hb.json"
        p.write_text(json.dumps({"ts": 42}))
        r = heartbeat_gate(path=p)
        assert r.allow is False


class TestPreTradeGate:
    def test_missing_state_allows(self, tmp_path: Path):
        r = pre_trade_pause_gate(path=tmp_path / "nope.json")
        assert r.allow is True

    def test_cold_allows(self, tmp_path: Path):
        p = tmp_path / "g.json"
        p.write_text(json.dumps({"state": "COLD"}))
        assert pre_trade_pause_gate(path=p).allow is True

    def test_hot_denies(self, tmp_path: Path):
        p = tmp_path / "g.json"
        p.write_text(json.dumps({"state": "HOT", "reason": "manual"}))
        r = pre_trade_pause_gate(path=p)
        assert r.allow is False
        assert "HOT" in r.reason

    def test_hot_expired_allows(self, tmp_path: Path):
        p = tmp_path / "g.json"
        past = (datetime.now(tz=UTC) - timedelta(minutes=5)).isoformat()
        p.write_text(json.dumps({"state": "HOT", "expires": past}))
        assert pre_trade_pause_gate(path=p).allow is True


class TestCorrelationGate:
    def test_empty_book_allows_small_trade(self):
        r = correlation_gate({}, "MNQ", 1, max_agg_beta=2.0)
        assert r.allow is True

    def test_large_exposure_denies(self):
        r = correlation_gate({"MNQ": 2, "ES": 1}, "MNQ", 1, max_agg_beta=2.0)
        assert r.allow is False
        assert "exceeds" in r.reason

    def test_short_exposure_is_symmetric(self):
        r = correlation_gate({"MNQ": -3}, "MNQ", -1, max_agg_beta=2.0)
        assert r.allow is False


class TestGovernorGate:
    def test_no_journal_allows(self, tmp_path: Path):
        r = governor_gate(journal=tmp_path / "nope.sqlite")
        assert r.allow is True


class TestDeadmanGate:
    def test_no_heartbeat_allows_bootstrap(self, tmp_path: Path):
        r = deadman_gate(heartbeat_path=tmp_path / "nope.json")
        assert r.allow is True

    def test_stale_heartbeat_triggers(self, tmp_path: Path):
        p = tmp_path / "hb.json"
        old = datetime.now(tz=UTC) - timedelta(seconds=10_000)
        p.write_text(json.dumps({"ts": old.isoformat()}))
        r = deadman_gate(heartbeat_path=p, cutoff_sec=600)
        assert r.allow is False


class TestDefaultChain:
    def test_default_chain_has_5_gates(self):
        chain = build_default_chain()
        assert len(chain.gates) == 5
        names = [g.name for g in chain.gates]
        assert names == ["heartbeat", "pre_trade_pause", "deadman", "correlation", "governor"]
