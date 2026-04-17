"""[REAL] State provider interfaces used by MCP tools.

Abstractions here let tests inject in-memory implementations without
standing up a real executor. The live executor will implement the same
interfaces in a later step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol


class NotWiredError(RuntimeError):
    """Raised when a tool queries state that the executor hasn't populated."""


# ---- executor state ----


class ExecutorStateProvider(Protocol):
    def get_state(self) -> dict[str, Any]: ...
    def get_session_pnl(self, spec_hash: str | None) -> dict[str, Any]: ...
    def get_recent_fills(self, since_iso: str, spec_hash: str | None) -> list[dict[str, Any]]: ...
    def get_risk_utilization(self) -> dict[str, Any]: ...
    def get_ws_health(self) -> dict[str, Any]: ...
    def get_open_orders(self, venue: str) -> list[dict[str, Any]]: ...


@dataclass
class InMemoryExecutorState:
    """Default implementation used when no live executor is attached.

    All state-dependent tools raise NotWiredError. `state_version` tracks
    how many times the executor pushed a state snapshot (0 = never).
    """

    state_version: int = 0
    _state: dict[str, Any] = field(default_factory=dict)
    _fills: list[dict[str, Any]] = field(default_factory=list)
    _risk: dict[str, Any] = field(default_factory=dict)
    _ws_health: dict[str, Any] = field(default_factory=dict)
    _pnl: dict[str, Any] = field(default_factory=dict)
    _open_orders: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def push_state(self, state: dict[str, Any]) -> None:
        self._state = dict(state)
        self.state_version += 1

    def push_fill(self, fill: dict[str, Any]) -> None:
        self._fills.append(dict(fill))

    def set_pnl(self, pnl: dict[str, Any]) -> None:
        self._pnl = dict(pnl)

    def set_risk(self, risk: dict[str, Any]) -> None:
        self._risk = dict(risk)

    def set_ws_health(self, hh: dict[str, Any]) -> None:
        self._ws_health = dict(hh)

    def set_open_orders(self, venue: str, orders: list[dict[str, Any]]) -> None:
        self._open_orders[venue] = [dict(o) for o in orders]

    # protocol methods
    def get_state(self) -> dict[str, Any]:
        if self.state_version == 0:
            raise NotWiredError("executor has never pushed a state snapshot")
        return dict(self._state)

    def get_session_pnl(self, spec_hash: str | None) -> dict[str, Any]:
        if not self._pnl:
            raise NotWiredError("executor has not reported session pnl yet")
        if spec_hash is None:
            return dict(self._pnl)
        filtered = {k: v for k, v in self._pnl.items() if k == spec_hash}
        return filtered or {"spec_hash": spec_hash, "trades": 0, "pnl_dollars": "0.00"}

    def get_recent_fills(self, since_iso: str, spec_hash: str | None) -> list[dict[str, Any]]:
        since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        out: list[dict[str, Any]] = []
        for f in self._fills:
            ts_raw = f.get("ts")
            if ts_raw is None:
                continue
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts < since:
                continue
            if spec_hash is not None and f.get("spec_hash") != spec_hash:
                continue
            out.append(dict(f))
        return out

    def get_risk_utilization(self) -> dict[str, Any]:
        if not self._risk:
            raise NotWiredError("risk manager has not reported utilization yet")
        return dict(self._risk)

    def get_ws_health(self) -> dict[str, Any]:
        if not self._ws_health:
            raise NotWiredError("ws health not reported yet")
        return dict(self._ws_health)

    def get_open_orders(self, venue: str) -> list[dict[str, Any]]:
        if venue not in self._open_orders:
            raise NotWiredError(f"no open-orders snapshot for venue {venue!r}")
        return [dict(o) for o in self._open_orders[venue]]


# ---- strategy repository ----


@dataclass
class StrategyRepository:
    """File-backed read-only strategy repository.

    Reads specs from `<root>/specs/strategies/*.yaml`. `version` in tool
    calls can be either a strategy id (e.g. `mnq_baseline_v0_1`) or a
    semver string (e.g. `0.1.0`) — resolved by scanning all specs.
    """

    root: Path

    @classmethod
    def default(cls) -> StrategyRepository:
        return cls(root=Path.cwd())

    @property
    def strategies_dir(self) -> Path:
        return self.root / "specs" / "strategies"

    def list_versions(self) -> list[dict[str, str]]:
        from mnq.spec.loader import load_spec

        out: list[dict[str, str]] = []
        if not self.strategies_dir.exists():
            return out
        for p in sorted(self.strategies_dir.glob("*.yaml")):
            try:
                spec = load_spec(p)
            except Exception as e:
                out.append({
                    "file": str(p.relative_to(self.root)),
                    "error": f"{type(e).__name__}: {e}",
                })
                continue
            out.append({
                "id": spec.strategy.id,
                "semver": spec.strategy.semver,
                "tier": spec.strategy.tier,
                "content_hash": spec.strategy.content_hash,
                "experimental": str(spec.strategy.experimental).lower(),
                "file": str(p.relative_to(self.root)),
            })
        return out

    def get(self, version: str) -> dict[str, Any]:
        from mnq.spec.loader import load_spec

        if not self.strategies_dir.exists():
            raise FileNotFoundError(f"no strategies dir at {self.strategies_dir}")
        for p in sorted(self.strategies_dir.glob("*.yaml")):
            try:
                spec = load_spec(p)
            except Exception:
                continue
            if spec.strategy.id == version or spec.strategy.semver == version:
                data = spec.model_dump(mode="json", exclude_none=True)
                data["_file"] = str(p.relative_to(self.root))
                result = _json_safe(data)
                assert isinstance(result, dict)
                return result
        raise KeyError(f"no spec matches {version!r}")


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj
