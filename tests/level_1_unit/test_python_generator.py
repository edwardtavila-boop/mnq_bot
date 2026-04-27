"""Level-1 tests for mnq.generators.python_exec."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from mnq.generators.python_exec import PythonGenerationError, render_python
from mnq.generators.python_exec.base import HistoryRing, StrategyBase
from mnq.spec.loader import load_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"


@pytest.fixture(scope="module")
def baseline_spec():
    return load_spec(BASELINE)


@pytest.fixture
def generated_module(baseline_spec, tmp_path):
    src = render_python(baseline_spec)
    out = tmp_path / "gen_strategy.py"
    out.write_text(src)
    spec_obj = importlib.util.spec_from_file_location("gen_strategy_mod", out)
    assert spec_obj is not None and spec_obj.loader is not None
    mod = importlib.util.module_from_spec(spec_obj)
    sys.modules["gen_strategy_mod"] = mod
    spec_obj.loader.exec_module(mod)
    return mod


class TestRenderPython:
    def test_header_comment_has_spec_id(self, baseline_spec) -> None:
        src = render_python(baseline_spec)
        assert baseline_spec.strategy.id in src

    def test_generated_file_imports_cleanly(self, generated_module) -> None:
        assert hasattr(generated_module, "GeneratedStrategy")
        assert hasattr(generated_module, "build")
        assert generated_module.SPEC_ID == "mnq_baseline_v0_1"

    def test_build_returns_instance(self, generated_module, baseline_spec) -> None:
        inst = generated_module.build(baseline_spec)
        assert isinstance(inst, StrategyBase)

    def test_byte_identical_snapshot(self, baseline_spec) -> None:
        a = render_python(baseline_spec)
        b = render_python(baseline_spec)
        assert a == b

    def test_disabled_generator_raises(self, baseline_spec) -> None:
        data = baseline_spec.model_dump()
        data["generators"]["python_executor"]["enabled"] = False
        from mnq.spec.schema import StrategySpec

        s2 = StrategySpec.model_validate(data)
        with pytest.raises(PythonGenerationError):
            render_python(s2)


class TestHistoryRing:
    def test_push_and_index(self) -> None:
        r = HistoryRing(capacity=4)
        for v in [1.0, 2.0, 3.0, 4.0]:
            r.push(v)
        assert r[0] == 4.0
        assert r[1] == 3.0
        assert r[2] == 2.0
        assert r[3] == 1.0

    def test_out_of_bounds_returns_none(self) -> None:
        r = HistoryRing()
        r.push(1.0)
        assert r[5] is None

    def test_rising(self) -> None:
        r = HistoryRing()
        for v in [1.0, 2.0, 3.0]:  # now-most-recent = 3.0
            r.push(v)
        assert r.rising(2) is True

    def test_not_rising_when_flat(self) -> None:
        r = HistoryRing()
        for v in [1.0, 1.0, 1.0]:
            r.push(v)
        assert r.rising(2) is False


class TestGeneratedOnSyntheticBars:
    def test_feeds_100_bars_without_error(self, generated_module, baseline_spec) -> None:
        from mnq.core.types import Bar

        inst = generated_module.build(baseline_spec)
        start = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)  # 09:30 NY
        signals: list = []
        for i in range(100):
            ts = start + timedelta(minutes=i)
            # Slight trending close to exercise EMAs
            p = 20000.0 + 0.25 * (i % 10)
            bar = Bar(
                ts=ts,
                open=Decimal(str(p)),
                high=Decimal(str(p + 0.25)),
                low=Decimal(str(p - 0.25)),
                close=Decimal(str(p)),
                volume=100 + (i % 5) * 10,
                timeframe_sec=60,
            )
            sig = inst.on_bar(bar)
            if sig is not None:
                signals.append(sig)
        # We don't assert signals > 0 because the baseline is deliberately
        # restrictive and synthetic data may not satisfy all gates.  We
        # assert only determinism + no exceptions.
        assert isinstance(signals, list)

    def test_determinism_across_two_runs(self, generated_module, baseline_spec) -> None:
        from mnq.core.types import Bar

        def run() -> list:
            inst = generated_module.build(baseline_spec)
            start = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
            out: list = []
            for i in range(120):
                ts = start + timedelta(minutes=i)
                # oscillating close crosses EMAs and the VWAP
                phase = (i // 8) % 2
                p = 20000.0 + (1.0 if phase else -1.0) * (i % 8) * 0.5
                bar = Bar(
                    ts=ts,
                    open=Decimal(str(p)),
                    high=Decimal(str(p + 0.5)),
                    low=Decimal(str(p - 0.5)),
                    close=Decimal(str(p)),
                    volume=100 + (i % 7) * 20,
                    timeframe_sec=60,
                )
                sig = inst.on_bar(bar)
                if sig is not None:
                    out.append(
                        (
                            sig.side,
                            int(sig.qty),
                            str(sig.ref_price),
                            str(sig.stop),
                            str(sig.take_profit),
                        )
                    )
            return out

        a = run()
        b = run()
        assert a == b
