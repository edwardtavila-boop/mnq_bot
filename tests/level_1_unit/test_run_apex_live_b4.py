"""Tests for B4 closure: per-bar Firm review wiring in
``scripts/run_eta_live.py::ApexRuntime``.

Pin the contract the operator's locked plan demanded:

  > Wire firm_runtime.run_six_stage_review per-bar on real tape.
  > PM REJECT verdicts must block the (placeholder) order intent and
  > increment ``orders_blocked``. ImportError on the shim must be
  > fail-open (latched + skipped, not raised).

Each test instantiates ApexRuntime directly with hand-built deps so we
don't depend on argparse, the journal SQLite, or the real firm package.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from mnq.core.types import Bar
from mnq.risk.tiered_rollout import TieredRollout

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run_eta_live.py"


@pytest.fixture(scope="module")
def runtime_mod() -> Any:
    """Load scripts/run_eta_live.py as a fresh module under a stable name."""
    spec = importlib.util.spec_from_file_location("run_eta_live_for_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eta_live_for_test"] = module
    spec.loader.exec_module(module)
    return module


def _make_bar(epoch: int, *, c: float = 21000.0) -> Bar:
    """Synthesize a Bar at a given UTC epoch, fixed OHLC."""
    return Bar(
        ts=datetime.fromtimestamp(epoch, tz=UTC),
        open=Decimal(str(c)),
        high=Decimal(str(c + 5)),
        low=Decimal(str(c - 5)),
        close=Decimal(str(c)),
        volume=100,
        timeframe_sec=300,
    )


def _bars_iter(n: int) -> Iterator[Bar]:
    """Yield n synthetic bars, 5min apart, starting 2024-01-02 13:30 UTC."""
    start = 1704202200
    for i in range(n):
        yield _make_bar(start + i * 300)


@dataclass
class _FakeBreakerDecision:
    allowed: bool = True
    reason: str = "ok"
    detail: str = ""


class _FakeBreaker:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow

    def allow_trade(self, *, now: datetime | None = None) -> _FakeBreakerDecision:
        if self.allow:
            return _FakeBreakerDecision(True, "ok", "")
        return _FakeBreakerDecision(False, "kill_switch", "test halt")


class _FakeBook:
    """Stand-in for OrderBook in tests; just tracks the gate-chain attr."""

    def __init__(self) -> None:
        self._gate_chain = object()  # truthy


class _FakeJournal:
    def close(self) -> None:
        pass


class _FakeKillSwitch:
    @property
    def path(self):
        return Path("kill_switch.flag")


def _make_runtime(
    runtime_mod: Any,
    *,
    n_bars: int,
    review_enabled: bool = True,
    review_every: int = 1,
    rollout_tier: int = 1,
    breaker_allow: bool = True,
):
    """Construct an ApexRuntime hand-wired with fakes + a synthetic tape."""
    cfg = runtime_mod.RuntimeConfig(
        live=False,
        max_bars=n_bars,
        tick_interval_s=0.0,
        variant="r5_real_wide_target",
        state_dir=Path("/tmp/_b4_test_state"),
        journal_path=Path("/tmp/_b4_test_state/journal.sqlite"),
        skip_promotion_gate=True,
        tape_path=None,
        firm_review_every=review_every,
        firm_review_enabled=review_enabled,
    )
    rollout = TieredRollout.initial(cfg.variant)
    rollout.tier = rollout_tier
    return runtime_mod.ApexRuntime(
        cfg=cfg,
        journal=_FakeJournal(),
        book=_FakeBook(),
        breaker=_FakeBreaker(allow=breaker_allow),
        rollout=rollout,
        tape=_bars_iter(n_bars),
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_firm_review_fires_per_bar_when_enabled(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With review_enabled=True and rollout at tier 1, every bar triggers
    a six-stage review and the PM verdict drives stats."""
    calls: list[dict] = []

    def _fake_review(**kwargs):
        calls.append(kwargs)
        return {"pm": {"verdict": "APPROVE", "probability": 0.7,
                       "reasoning": "ok"}}

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review", _fake_review, raising=True,
    )
    # compute_confluence is fine to keep real (it's stub-fast); but make
    # sure it doesn't raise.
    runtime = _make_runtime(runtime_mod, n_bars=3)
    rc = asyncio.run(runtime.run())
    assert rc == runtime_mod.EX_OK
    assert runtime.stats.bars_processed == 3
    assert runtime.stats.firm_reviews_run == 3, (
        f"expected 3 reviews, got {runtime.stats.firm_reviews_run}"
    )
    assert runtime.stats.firm_approved == 3
    assert runtime.stats.firm_rejected == 0
    assert len(calls) == 3
    # Each call should have a bar payload with sensible values
    for c in calls:
        assert c["strategy_id"] == "r5_real_wide_target"
        assert "bar" in c["payload"]
        assert "close" in c["payload"]["bar"]


def test_firm_reject_blocks_order(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PM REJECT verdict increments orders_blocked and firm_rejected."""

    def _fake_review(**_):
        return {"pm": {"verdict": "REJECT", "probability": 0.2,
                       "reasoning": "too risky"}}

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review", _fake_review, raising=True,
    )
    runtime = _make_runtime(runtime_mod, n_bars=2)
    asyncio.run(runtime.run())
    assert runtime.stats.firm_reviews_run == 2
    assert runtime.stats.firm_rejected == 2
    assert runtime.stats.firm_approved == 0
    assert runtime.stats.orders_blocked == 2


def test_firm_kill_verdict_also_blocks(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KILL / BLOCK PM verdicts route through the same block path as REJECT."""
    def _fake_review(**_):
        return {"pm": {"verdict": "KILL", "probability": 0.0,
                       "reasoning": "emergency stop"}}

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review", _fake_review, raising=True,
    )
    runtime = _make_runtime(runtime_mod, n_bars=1)
    asyncio.run(runtime.run())
    assert runtime.stats.firm_rejected == 1
    assert runtime.stats.orders_blocked == 1


def test_firm_review_disabled_skips_review(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``--no-firm-review``, no shim call happens even when bars flow."""
    called = {"n": 0}

    def _fake_review(**_):
        called["n"] += 1
        return {"pm": {"verdict": "APPROVE", "probability": 1.0}}

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review", _fake_review, raising=True,
    )
    runtime = _make_runtime(runtime_mod, n_bars=3, review_enabled=False)
    asyncio.run(runtime.run())
    assert called["n"] == 0
    assert runtime.stats.firm_reviews_run == 0
    assert runtime.stats.bars_processed == 3


def test_firm_review_every_n(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """review_every=3 fires exactly on bars 0, 3, 6, ..."""
    fired_at: list[int] = []
    counter = {"i": -1}

    def _fake_review(**kwargs):
        counter["i"] += 1
        # We need to know which bar this is; the decision_context has it.
        ctx = kwargs.get("decision_context", "")
        fired_at.append(counter["i"])
        return {"pm": {"verdict": "APPROVE", "probability": 0.5,
                       "reasoning": ctx}}

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review", _fake_review, raising=True,
    )
    runtime = _make_runtime(runtime_mod, n_bars=10, review_every=3)
    asyncio.run(runtime.run())
    # bars 0, 3, 6, 9 -> 4 reviews
    assert runtime.stats.firm_reviews_run == 4


def test_firm_shim_import_error_fail_open(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``mnq.firm_runtime`` fails to import, the runtime latches and
    keeps draining bars; no exception escapes to the caller."""
    import builtins
    real_import = builtins.__import__

    def _raise_for_firm_runtime(name, *args, **kwargs):
        if name == "mnq.firm_runtime":
            raise ImportError("simulated missing firm package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raise_for_firm_runtime)
    runtime = _make_runtime(runtime_mod, n_bars=3)
    rc = asyncio.run(runtime.run())
    assert rc == runtime_mod.EX_OK
    assert runtime.stats.bars_processed == 3
    assert runtime.stats.firm_reviews_run == 0
    # Latch should be set so subsequent bars don't keep retrying.
    assert runtime._firm_shim_unavailable is True  # noqa: SLF001


def test_rollout_qty_zero_skips_review(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollout HALT / qty=0 short-circuits BEFORE the firm review. We
    don't want to spend compute reviewing when no order could flow."""
    called = {"n": 0}

    def _fake_review(**_):
        called["n"] += 1
        return {"pm": {"verdict": "APPROVE", "probability": 1.0}}

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review", _fake_review, raising=True,
    )
    runtime = _make_runtime(runtime_mod, n_bars=3, rollout_tier=0)
    asyncio.run(runtime.run())
    assert called["n"] == 0
    assert runtime.stats.bars_processed == 3
    assert runtime.stats.firm_reviews_run == 0


def test_breaker_block_skips_review(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Circuit breaker block also short-circuits before the review."""
    called = {"n": 0}

    def _fake_review(**_):
        called["n"] += 1
        return {"pm": {"verdict": "APPROVE", "probability": 1.0}}

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review", _fake_review, raising=True,
    )
    runtime = _make_runtime(runtime_mod, n_bars=3, breaker_allow=False)
    asyncio.run(runtime.run())
    assert called["n"] == 0
    assert runtime.stats.firm_reviews_run == 0
    assert runtime.stats.orders_blocked == 3


def test_tape_exhausted_drains_cleanly(
    runtime_mod: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the tape iterator runs out, the runtime exits with EX_OK."""

    def _fake_review(**_):
        return {"pm": {"verdict": "APPROVE", "probability": 0.8}}

    monkeypatch.setattr(
        "mnq.firm_runtime.run_six_stage_review", _fake_review, raising=True,
    )
    # Tape has only 2 bars, but max_bars=10. Expect drain after 2.
    cfg = runtime_mod.RuntimeConfig(
        live=False, max_bars=10, tick_interval_s=0.0,
        variant="r5_real_wide_target",
        state_dir=Path("/tmp/_b4_test_state"),
        journal_path=Path("/tmp/_b4_test_state/journal.sqlite"),
        skip_promotion_gate=True,
        tape_path=None, firm_review_every=1, firm_review_enabled=True,
    )
    rollout = TieredRollout.initial(cfg.variant)
    rollout.tier = 1
    runtime = runtime_mod.ApexRuntime(
        cfg=cfg, journal=_FakeJournal(), book=_FakeBook(),
        breaker=_FakeBreaker(allow=True), rollout=rollout,
        tape=_bars_iter(2),
    )
    asyncio.run(runtime.run())
    assert runtime.stats.bars_processed == 2
    assert runtime.stats.firm_reviews_run == 2
