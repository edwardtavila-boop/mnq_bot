"""Level-1 tests for mnq.mcp.*."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnq.mcp.server import build_server, registered_tool_names
from mnq.mcp.state import InMemoryExecutorState, NotWiredError, StrategyRepository
from mnq.mcp.tools.read_only import build_read_only_tools

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestStrategyRepository:
    def test_list_versions_finds_baseline(self) -> None:
        repo = StrategyRepository(root=REPO_ROOT)
        versions = repo.list_versions()
        ids = [v.get("id") for v in versions]
        assert "mnq_baseline_v0_1" in ids

    def test_get_by_id(self) -> None:
        repo = StrategyRepository(root=REPO_ROOT)
        spec = repo.get("mnq_baseline_v0_1")
        assert spec["strategy"]["id"] == "mnq_baseline_v0_1"
        assert spec["instrument"]["symbol"] == "MNQ"

    def test_get_by_semver(self) -> None:
        repo = StrategyRepository(root=REPO_ROOT)
        spec = repo.get("0.1.0")
        assert spec["strategy"]["semver"] == "0.1.0"

    def test_get_missing_raises(self) -> None:
        repo = StrategyRepository(root=REPO_ROOT)
        with pytest.raises(KeyError):
            repo.get("nope")


class TestExecutorStateStub:
    def test_get_state_not_wired(self) -> None:
        s = InMemoryExecutorState()
        with pytest.raises(NotWiredError):
            s.get_state()

    def test_get_state_after_push(self) -> None:
        s = InMemoryExecutorState()
        s.push_state({"position": 0, "bars_processed": 42})
        state = s.get_state()
        assert state["bars_processed"] == 42

    def test_recent_fills_filters_since(self) -> None:
        s = InMemoryExecutorState()
        now = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
        s.push_fill({"ts": (now - timedelta(minutes=5)).isoformat(), "spec_hash": "h", "px": "100"})
        s.push_fill({"ts": now.isoformat(), "spec_hash": "h", "px": "101"})
        s.push_fill(
            {"ts": (now + timedelta(minutes=5)).isoformat(), "spec_hash": "h2", "px": "102"}
        )
        out = s.get_recent_fills(since_iso=now.isoformat(), spec_hash="h")
        assert len(out) == 1
        assert out[0]["px"] == "101"

    def test_open_orders_not_wired(self) -> None:
        s = InMemoryExecutorState()
        with pytest.raises(NotWiredError):
            s.get_open_orders("tradovate_paper")


class TestReadOnlyToolsContract:
    def _tools(self):
        state = InMemoryExecutorState()
        repo = StrategyRepository(root=REPO_ROOT)
        return state, repo, {name: fn for name, fn, _ in build_read_only_tools(state, repo)}

    def test_all_eight_tools_present(self) -> None:
        _, _, tools = self._tools()
        expected = {
            "get_strategy",
            "list_strategy_versions",
            "get_executor_state",
            "get_session_pnl",
            "get_recent_fills",
            "get_risk_utilization",
            "get_ws_health",
            "get_open_orders",
        }
        assert set(tools.keys()) == expected

    def test_list_strategy_versions_returns_list(self) -> None:
        _, _, tools = self._tools()
        r = tools["list_strategy_versions"]()
        assert r["ok"] is True
        assert any(v.get("id") == "mnq_baseline_v0_1" for v in r["data"])

    def test_get_strategy_ok(self) -> None:
        _, _, tools = self._tools()
        r = tools["get_strategy"]("mnq_baseline_v0_1")
        assert r["ok"] is True
        assert r["data"]["strategy"]["id"] == "mnq_baseline_v0_1"

    def test_get_strategy_missing_is_structured(self) -> None:
        _, _, tools = self._tools()
        r = tools["get_strategy"]("nope")
        assert r["ok"] is False
        assert r["error"] == "not_found"

    def test_executor_state_not_wired_is_structured(self) -> None:
        _, _, tools = self._tools()
        r = tools["get_executor_state"]()
        assert r["ok"] is False
        assert r["error"] == "not_wired"

    def test_session_pnl_not_wired(self) -> None:
        _, _, tools = self._tools()
        assert tools["get_session_pnl"]()["error"] == "not_wired"

    def test_recent_fills_returns_empty_when_no_data(self) -> None:
        _, _, tools = self._tools()
        # No NotWiredError here — the underlying method returns [] when
        # no fills have been pushed.
        r = tools["get_recent_fills"](since="2026-01-01T00:00:00Z")
        assert r["ok"] is True
        assert r["data"] == []

    def test_risk_utilization_not_wired(self) -> None:
        _, _, tools = self._tools()
        assert tools["get_risk_utilization"]()["error"] == "not_wired"

    def test_ws_health_not_wired(self) -> None:
        _, _, tools = self._tools()
        assert tools["get_ws_health"]()["error"] == "not_wired"

    def test_open_orders_not_wired(self) -> None:
        _, _, tools = self._tools()
        assert tools["get_open_orders"](venue="tradovate_paper")["error"] == "not_wired"

    def test_populated_state_surfaces_through_tools(self) -> None:
        state, repo, tools = self._tools()
        state.push_state({"position": 1, "bars_processed": 100, "spec_hash": "sha256:abc"})
        r = tools["get_executor_state"]()
        assert r["ok"] is True
        assert r["data"]["bars_processed"] == 100


class TestServerBuilds:
    def test_build_server_registers_eight_tools(self) -> None:
        s = build_server(repo=StrategyRepository(root=REPO_ROOT))
        names = registered_tool_names(s)
        assert len(names) == 8
        assert "get_strategy" in names
        assert "list_strategy_versions" in names
