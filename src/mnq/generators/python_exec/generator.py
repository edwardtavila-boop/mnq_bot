"""[REAL] Python-executor source generator.

Emits a `.py` module that:
    - Defines a subclass of `StrategyBase` named `GeneratedStrategy`.
    - Implements `_eval_long`, `_eval_short`, `_compute_stop_ticks`.
    - Provides a `build()` factory that wires feature instances from the
      spec. The executor calls `build(spec)` to get a ready strategy.
    - Holds the spec's content_hash in a module-level constant for
      divergence detection.

The generated file imports only from the stdlib, `mnq.core.types`,
`mnq.features`, and `mnq.generators.python_exec.base`. No third-party
imports, no side effects at import time.
"""

from __future__ import annotations

import re
from typing import Any

from mnq.spec import ast as ast_mod
from mnq.spec.ast import (
    And,
    BarsSinceEntry,
    BarsSinceOpen,
    Builtin,
    Comparison,
    Falling,
    FeatureRef,
    Flat,
    InBlackout,
    InPosition,
    Not,
    Number,
    Or,
    Rising,
    SessionWindowIn,
    Visitor,
)
from mnq.spec.schema import (
    ATR,
    EMA,
    RMA,
    SMA,
    VWAP,
    RelativeVolume,
    StrategySpec,
)


class PythonGenerationError(ValueError):
    """Raised when a spec cannot be rendered to Python source."""


_BUILTIN_SRC_MAP = {
    "open": "float(ctx.bar.open)",
    "high": "float(ctx.bar.high)",
    "low": "float(ctx.bar.low)",
    "close": "float(ctx.bar.close)",
    "volume": "float(ctx.bar.volume)",
    "hl2": "((float(ctx.bar.high) + float(ctx.bar.low)) / 2.0)",
    "hlc3": "((float(ctx.bar.high) + float(ctx.bar.low) + float(ctx.bar.close)) / 3.0)",
    "ohlc4": "((float(ctx.bar.open) + float(ctx.bar.high) + float(ctx.bar.low) + float(ctx.bar.close)) / 4.0)",
}


class _PyExprVisitor(Visitor):
    def __init__(self) -> None:
        pass

    # combinators
    def visit_Or(self, node: Or) -> str:
        if not node.children_:
            return "True"
        return "(" + " or ".join(self.visit(c) for c in node.children_) + ")"

    def visit_And(self, node: And) -> str:
        if not node.children_:
            return "True"
        return "(" + " and ".join(self.visit(c) for c in node.children_) + ")"

    def visit_Not(self, node: Not) -> str:
        return "(not " + str(self.visit(node.child)) + ")"

    # operands
    def visit_FeatureRef(self, node: FeatureRef) -> str:
        return f"ctx.f({node.name!r})"

    def visit_Builtin(self, node: Builtin) -> str:
        expr = _BUILTIN_SRC_MAP.get(node.name)
        if expr is None:
            raise PythonGenerationError(f"unknown builtin: {node.name!r}")
        return expr

    def visit_Number(self, node: Number) -> str:
        return repr(float(node.value))

    # comparisons / modifiers
    def visit_Comparison(self, node: Comparison) -> str:
        op = node.op
        left = self.visit(node.left)
        right = self.visit(node.right)

        if op in (">", "<", ">=", "<=", "==", "!="):
            if node.for_bars is not None:
                n = int(node.for_bars)
                l_spec = _history_accessor(node.left)
                r_spec = _history_accessor(node.right)
                return f"_for_bars_cmp({l_spec}, {r_spec}, {op!r}, {n}, ctx)"
            if node.on_bar is not None:
                n = int(node.on_bar)
                l_spec = _history_accessor(node.left)
                r_spec = _history_accessor(node.right)
                return f"_on_bar_cmp({l_spec}, {r_spec}, {op!r}, {n}, ctx)"
            return f"({left} {op} {right})"

        if op in ("crosses_above", "crosses_below", "crosses"):
            l_spec = _history_accessor(node.left)
            r_spec = _history_accessor(node.right)
            within = int(node.within_bars) if node.within_bars is not None else 1
            fn = {
                "crosses_above": "_crossed_above",
                "crosses_below": "_crossed_below",
                "crosses": "_crossed",
            }[op]
            return f"{fn}({l_spec}, {r_spec}, {within}, ctx)"

        raise PythonGenerationError(f"unsupported comparator: {op!r}")

    # predicates
    def visit_SessionWindowIn(self, node: SessionWindowIn) -> str:
        names = [repr(n) for n in node.names]
        return f"(ctx.session_window in ({', '.join(names)}))"

    def visit_InBlackout(self, _: InBlackout) -> str:
        return "ctx.in_blackout"

    def visit_InPosition(self, _: InPosition) -> str:
        return "(ctx.position_size != 0)"

    def visit_Flat(self, _: Flat) -> str:
        return "(ctx.position_size == 0)"

    def visit_BarsSinceOpen(self, node: BarsSinceOpen) -> str:
        return f"(ctx.bars_since_session_open {node.op} {int(node.value)})"

    def visit_BarsSinceEntry(self, node: BarsSinceEntry) -> str:
        return f"(ctx.bars_since_entry {node.op} {int(node.value)})"

    def visit_Rising(self, node: Rising) -> str:
        return f"ctx.hist({node.feature.name!r}).rising({int(node.for_bars)})"

    def visit_Falling(self, node: Falling) -> str:
        return f"ctx.hist({node.feature.name!r}).falling({int(node.for_bars)})"


def _history_accessor(node: Any) -> str:
    """Return a tuple-style spec `(kind, key)` used by the runtime cmp helpers.

    kind="f" + key=feature_id -> look up via ctx.hist(feature)
    kind="b" + key=builtin     -> scalar; cmp helpers fall back to ctx.bar
    kind="n" + key=number      -> scalar
    """
    if isinstance(node, FeatureRef):
        return f"('f', {node.name!r})"
    if isinstance(node, Builtin):
        return f"('b', {node.name!r})"
    if isinstance(node, Number):
        return f"('n', {float(node.value)!r})"
    raise PythonGenerationError(
        f"comparator operand must be feature/builtin/number, got {type(node).__name__}"
    )


def _mirror_condition_str(cond: str) -> str:
    """Same textual mirror used by the Pine generator."""
    subs = [
        (r"\s>\s", " __LT__ "),
        (r"\s<\s", " __GT__ "),
        (r"\s>=\s", " __LE__ "),
        (r"\s<=\s", " __GE__ "),
        (r"crosses_above", "__XB__"),
        (r"crosses_below", "__XA__"),
        (r"\brising\b", "__FALL__"),
        (r"\bfalling\b", "__RISE__"),
    ]
    out = cond
    for pat, rep in subs:
        out = re.sub(pat, rep, out)
    restore = {
        " __LT__ ": " < ",
        " __GT__ ": " > ",
        " __LE__ ": " <= ",
        " __GE__ ": " >= ",
        "__XB__": "crosses_below",
        "__XA__": "crosses_above",
        "__FALL__": "falling",
        "__RISE__": "rising",
    }
    for k, v in restore.items():
        out = out.replace(k, v)
    return out


def _resolve_conds(spec: StrategySpec, side: str) -> tuple[list[str], str]:
    entry = spec.entry.long if side == "long" else spec.entry.short
    other = spec.entry.short if side == "long" else spec.entry.long
    target = entry
    if target.mirror_of is not None:
        target = other
        if target.mirror_of is not None:
            raise PythonGenerationError("mirror_of chain detected")

    # If the current side says mirror_of, we also textually flip.
    if entry.mirror_of is not None:
        if target.all_of is not None:
            return [_mirror_condition_str(c) for c in target.all_of], "all_of"
        if target.any_of is not None:
            return [_mirror_condition_str(c) for c in target.any_of], "any_of"
        if target.n_of is not None:
            k, conds = target.n_of
            return [_mirror_condition_str(c) for c in conds], f"n_of:{k}"

    if target.all_of is not None:
        return list(target.all_of), "all_of"
    if target.any_of is not None:
        return list(target.any_of), "any_of"
    if target.n_of is not None:
        k, conds = target.n_of
        return list(conds), f"n_of:{k}"
    raise PythonGenerationError(f"entry.{side}: no conditions")


def _compose(parts: list[str], combinator: str) -> str:
    if not parts:
        return "False"
    if combinator == "all_of":
        return "(" + " and ".join(parts) + ")"
    if combinator == "any_of":
        return "(" + " or ".join(parts) + ")"
    if combinator.startswith("n_of:"):
        k = int(combinator.split(":", 1)[1])
        sums = " + ".join(f"(1 if {p} else 0)" for p in parts)
        return f"(({sums}) >= {k})"
    raise PythonGenerationError(f"unknown combinator: {combinator!r}")


def _feature_build_line(f: Any) -> str:
    if isinstance(f, EMA):
        base = f"EMA(length={int(f.length)}, source={f.source!r})"
    elif isinstance(f, SMA):
        base = f"SMA(length={int(f.length)}, source={f.source!r})"
    elif isinstance(f, RMA):
        base = f"RMA(length={int(f.length)}, source={f.source!r})"
    elif isinstance(f, ATR):
        base = f"ATR(length={int(f.length)})"
    elif isinstance(f, VWAP):
        base = f"VWAP(anchor={f.anchor!r})"
    elif isinstance(f, RelativeVolume):
        base = f"RelativeVolume(length={int(f.length)})"
    else:
        raise PythonGenerationError(
            f"feature type not supported in Python generator: {type(f).__name__}"
        )

    if f.timeframe is not None and f.timeframe != "" and f.timeframe != "primary":
        return f"HTFWrapper({base}, timeframe={f.timeframe!r})"
    return base


def render_python(spec: StrategySpec) -> str:
    """Render a StrategySpec to a self-contained Python module."""
    if not spec.generators.python_executor.enabled:
        raise PythonGenerationError("spec.generators.python_executor.enabled is false")

    visitor = _PyExprVisitor()
    long_conds, long_comb = _resolve_conds(spec, "long")
    short_conds, short_comb = _resolve_conds(spec, "short")
    long_parts = [visitor.visit(ast_mod.parse(c)) for c in long_conds]
    short_parts = [visitor.visit(ast_mod.parse(c)) for c in short_conds]
    long_body = _compose(long_parts, long_comb)
    short_body = _compose(short_parts, short_comb)

    # Stop distance expr
    stop = spec.exit.initial_stop
    if stop.type == "atr_multiple":
        if stop.feature is None or stop.multiplier is None:
            raise PythonGenerationError("atr_multiple stop requires feature + multiplier")
        tick = repr(float(spec.instrument.tick_size))
        mult = repr(float(stop.multiplier))
        stop_expr = f"int(round((ctx.f({stop.feature!r}) * {mult}) / {tick}))"
    elif stop.type == "fixed_ticks":
        if stop.ticks is None:
            raise PythonGenerationError("fixed_ticks stop requires ticks")
        stop_expr = f"{int(stop.ticks)}"
    else:
        stop_expr = "10  # swing_low_high placeholder"

    features_lines = ",\n        ".join(
        f"{f.id!r}: {_feature_build_line(f)}" for f in spec.features
    )

    source = f'''\
"""AUTO-GENERATED from spec {spec.strategy.id} v{spec.strategy.semver}.

DO NOT EDIT BY HAND. Regenerate via:
    mnq spec render specs/strategies/{spec.strategy.id}.yaml --target python

content_hash: {spec.strategy.content_hash or "<unhashed>"}
"""
from __future__ import annotations

from mnq.features import ATR, EMA, HTFWrapper, RMA, RelativeVolume, SMA, VWAP
from mnq.generators.python_exec.base import BarCtx, HistoryRing, StrategyBase

SPEC_HASH = {spec.strategy.content_hash!r}
SPEC_ID = {spec.strategy.id!r}
SPEC_SEMVER = {spec.strategy.semver!r}


# ---- AST-level comparison helpers ----
# These are used by the generated _eval_* methods.  They live at module
# scope so they're import-time cheap and deterministic.

def _resolve(spec_tuple, ctx, idx):
    """Resolve a (kind, key) spec to the scalar value at bar-offset idx.

    `idx=0` = current bar; `idx=1` = one bar ago, etc.  Returns None if
    the value isn't available yet.
    """
    kind, key = spec_tuple
    if kind == 'n':
        return key
    if kind == 'f':
        ring = ctx.hist(key)
        return ring[idx]
    if kind == 'b':
        if idx == 0:
            from mnq.features._source import price_from_source
            return price_from_source(ctx.bar, key)
        # past builtin: we don't keep bar history, so fall back to None
        return None
    return None


def _cmp(a, b, op):
    if a is None or b is None:
        return False
    if op == '>': return a > b
    if op == '<': return a < b
    if op == '>=': return a >= b
    if op == '<=': return a <= b
    if op == '==': return a == b
    if op == '!=': return a != b
    return False


def _for_bars_cmp(left_spec, right_spec, op, n, ctx):
    for i in range(n):
        a = _resolve(left_spec, ctx, i)
        b = _resolve(right_spec, ctx, i)
        if not _cmp(a, b, op):
            return False
    return True


def _on_bar_cmp(left_spec, right_spec, op, n, ctx):
    return _cmp(_resolve(left_spec, ctx, n), _resolve(right_spec, ctx, n), op)


def _crossed_above(left_spec, right_spec, within, ctx):
    for i in range(within):
        a_now, a_prev = _resolve(left_spec, ctx, i), _resolve(left_spec, ctx, i + 1)
        b_now, b_prev = _resolve(right_spec, ctx, i), _resolve(right_spec, ctx, i + 1)
        if None in (a_now, a_prev, b_now, b_prev):
            continue
        if a_prev <= b_prev and a_now > b_now:
            return True
    return False


def _crossed_below(left_spec, right_spec, within, ctx):
    for i in range(within):
        a_now, a_prev = _resolve(left_spec, ctx, i), _resolve(left_spec, ctx, i + 1)
        b_now, b_prev = _resolve(right_spec, ctx, i), _resolve(right_spec, ctx, i + 1)
        if None in (a_now, a_prev, b_now, b_prev):
            continue
        if a_prev >= b_prev and a_now < b_now:
            return True
    return False


def _crossed(left_spec, right_spec, within, ctx):
    return (_crossed_above(left_spec, right_spec, within, ctx)
            or _crossed_below(left_spec, right_spec, within, ctx))


class GeneratedStrategy(StrategyBase):
    """Generated from {spec.strategy.id} v{spec.strategy.semver}."""

    def _eval_long(self, ctx: BarCtx) -> bool:
        try:
            return bool({long_body})
        except RuntimeError:
            # A feature referenced by the condition isn't ready yet.
            return False

    def _eval_short(self, ctx: BarCtx) -> bool:
        try:
            return bool({short_body})
        except RuntimeError:
            return False

    def _compute_stop_ticks(self, ctx: BarCtx) -> int:
        try:
            return {stop_expr}
        except RuntimeError:
            return self.spec.exit.initial_stop.min_ticks


def build(spec) -> GeneratedStrategy:
    """Wire a GeneratedStrategy from the given StrategySpec instance.

    The spec is assumed to equal the one this module was generated from;
    the executor verifies `spec.strategy.content_hash == SPEC_HASH` before
    calling.
    """
    if spec.strategy.content_hash and SPEC_HASH and spec.strategy.content_hash != SPEC_HASH:
        raise ValueError(
            f"spec hash {{spec.strategy.content_hash}} != generator hash {{SPEC_HASH}}"
        )
    features = {{
        {features_lines}
    }}
    return GeneratedStrategy(spec=spec, features=features)
'''
    return source
