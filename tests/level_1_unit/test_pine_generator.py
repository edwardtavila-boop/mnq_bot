"""Level-1 tests for mnq.generators.pine.generator."""

from __future__ import annotations

from pathlib import Path

import pytest

from mnq.generators.pine import (
    PineGenerationError,
    PineStaticCheckError,
    render_pine,
    static_check_pine,
)
from mnq.generators.pine.generator import (
    PineExprVisitor,
    _compose_conditions,
    _mirror_condition_str,
    _pine_ident,
    _pine_num,
    _pine_str,
)
from mnq.spec import ast as ast_mod
from mnq.spec.loader import load_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "specs" / "strategies" / "v0_1_baseline.yaml"


@pytest.fixture(scope="module")
def baseline_spec():
    return load_spec(BASELINE)


class TestLowLevelHelpers:
    def test_pine_str_escapes_backslash_and_quote(self) -> None:
        assert _pine_str('a"b\\c') == '"a\\"b\\\\c"'

    def test_pine_num_int_gets_decimal(self) -> None:
        assert _pine_num(1) == "1.0"

    def test_pine_num_float_roundtrips(self) -> None:
        assert _pine_num(1.5) == "1.5"

    def test_pine_ident_sanitizes_non_alnum(self) -> None:
        assert _pine_ident("foo-bar.baz") == "foo_bar_baz"

    def test_pine_ident_leading_digit_prefixed(self) -> None:
        assert _pine_ident("9foo") == "_9foo"

    def test_mirror_flips_relational(self) -> None:
        assert _mirror_condition_str("feature:a > feature:b") == "feature:a < feature:b"
        assert _mirror_condition_str("feature:a crosses_above feature:b") == (
            "feature:a crosses_below feature:b"
        )
        assert (
            _mirror_condition_str("rising feature:x for_bars 2") == "falling feature:x for_bars 2"
        )


class TestVisitor:
    def setup_method(self) -> None:
        self.feat = {"ema_fast": "_f_ema_fast", "ema_slow": "_f_ema_slow", "htf_trend": "_f_htf"}
        self.v = PineExprVisitor(feature_vars=self.feat, side="long")

    def test_simple_gt(self) -> None:
        n = ast_mod.parse("feature:ema_fast > feature:ema_slow")
        assert self.v.visit(n) == "(_f_ema_fast > _f_ema_slow)"

    def test_crosses_above(self) -> None:
        n = ast_mod.parse("feature:ema_fast crosses_above feature:ema_slow")
        assert self.v.visit(n) == "ta.crossover(_f_ema_fast, _f_ema_slow)"

    def test_crosses_within_bars(self) -> None:
        n = ast_mod.parse("feature:ema_fast crosses_above feature:ema_slow within_bars 3")
        got = self.v.visit(n)
        assert "ta.crossover" in got and "3" in got

    def test_rising_feature(self) -> None:
        n = ast_mod.parse("rising feature:htf_trend for_bars 2")
        assert self.v.visit(n) == "ta.rising(_f_htf, 2)"

    def test_session_window_in(self) -> None:
        n = ast_mod.parse("session_window in [ rth_open_drive , afternoon ]")
        assert self.v.visit(n) == "(_sw_rth_open_drive or _sw_afternoon)"

    def test_flat_maps_to_pos_zero(self) -> None:
        n = ast_mod.parse("flat")
        assert self.v.visit(n) == "(strategy.position_size == 0)"

    def test_unknown_feature_raises(self) -> None:
        n = ast_mod.parse("feature:missing > 1")
        with pytest.raises(PineGenerationError):
            self.v.visit(n)

    def test_not_combinator(self) -> None:
        n = ast_mod.parse("not in_blackout")
        assert self.v.visit(n) == "(not _in_blackout)"

    def test_compose_all_of(self) -> None:
        parts = _compose_conditions(
            ["feature:ema_fast > feature:ema_slow", "close > feature:ema_fast"],
            "all_of",
            PineExprVisitor(
                feature_vars={"ema_fast": "_f_ema_fast", "ema_slow": "_f_ema_slow"},
                side="long",
            ),
        )
        assert " and " in parts and parts.startswith("(") and parts.endswith(")")

    def test_compose_n_of(self) -> None:
        parts = _compose_conditions(
            ["feature:ema_fast > feature:ema_slow", "close > feature:ema_fast"],
            "n_of:1",
            PineExprVisitor(
                feature_vars={"ema_fast": "_f_ema_fast", "ema_slow": "_f_ema_slow"},
                side="long",
            ),
        )
        assert ">= 1" in parts and " + " in parts


class TestRenderBaseline:
    def test_renders_without_error(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        assert src.splitlines()[0] == "//@version=6"

    def test_has_bar_magnifier(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        assert "use_bar_magnifier = true" in src
        assert "process_orders_on_close = false" in src

    def test_htf_uses_lookahead_off(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        assert "barmerge.lookahead_off" in src
        assert "lookahead_on" not in src

    def test_no_raw_security_call(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        # All security() references must be request.security()
        import re as _re

        for m in _re.finditer(r"security\s*\(", src):
            start = max(0, m.start() - len("request."))
            assert src[start : m.start()] == "request."

    def test_no_strategy_risk_calls(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        assert "strategy.risk." not in src

    def test_spec_id_appears_in_comment_header(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        assert baseline_spec.strategy.id in src

    def test_feature_vars_emitted(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        for f in baseline_spec.features:
            assert f"_f_{f.id}" in src

    def test_entry_alert_json_shape(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        assert "schema_version" in src
        assert '\\"event\\":\\"entry\\"' in src
        assert "alert(_entry_json" in src

    def test_mirror_short_renders(self, baseline_spec) -> None:
        # spec has short.mirror_of = long; render should produce a short block too
        src = render_pine(baseline_spec)
        assert "_short_entry" in src
        assert "strategy.short" in src

    def test_byte_identical_snapshot(self, baseline_spec) -> None:
        a = render_pine(baseline_spec)
        b = render_pine(baseline_spec)
        assert a == b

    def test_stop_tp_wired(self, baseline_spec) -> None:
        src = render_pine(baseline_spec)
        assert "_stop_ticks" in src
        assert "_tp_ticks" in src


class TestStaticCheck:
    def test_rejects_missing_version(self) -> None:
        with pytest.raises(PineStaticCheckError):
            static_check_pine("// no version tag\n")

    def test_rejects_lookahead_on(self) -> None:
        bad = (
            "//@version=6\n"
            'strategy(title="x", use_bar_magnifier = true, process_orders_on_close = false)\n'
            'v = request.security(syminfo.tickerid, "5", close, '
            "lookahead = barmerge.lookahead_on)\n"
        )
        with pytest.raises(PineStaticCheckError):
            static_check_pine(bad)

    def test_rejects_raw_security(self) -> None:
        bad = (
            "//@version=6\n"
            'strategy(title="x", use_bar_magnifier = true, process_orders_on_close = false)\n'
            'v = security(syminfo.tickerid, "5", close)\n'
        )
        with pytest.raises(PineStaticCheckError):
            static_check_pine(bad)

    def test_rejects_strategy_risk_call(self) -> None:
        bad = (
            "//@version=6\n"
            'strategy(title="x", use_bar_magnifier = true, process_orders_on_close = false)\n'
            "strategy.risk.max_drawdown(1000, strategy.cash)\n"
        )
        with pytest.raises(PineStaticCheckError):
            static_check_pine(bad)

    def test_rejects_missing_bar_magnifier(self) -> None:
        bad = '//@version=6\nstrategy(title="x", process_orders_on_close = false)\n'
        with pytest.raises(PineStaticCheckError):
            static_check_pine(bad)


class TestRenderErrors:
    def test_raises_when_pine_disabled(self, baseline_spec) -> None:
        data = baseline_spec.model_dump()
        data["generators"]["pine"]["enabled"] = False
        from mnq.spec.schema import StrategySpec

        s2 = StrategySpec.model_validate(data)
        with pytest.raises(PineGenerationError):
            render_pine(s2)
