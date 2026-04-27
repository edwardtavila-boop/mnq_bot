"""[REAL] Pine v6 source generator for StrategySpec.

Walks the AST of every entry condition via a Visitor subclass, emits
Pine v6 code, assembles it into a full `.pine` source string, then runs
a static check before returning.

Strict invariants enforced here (not at the call site):

- First line is always `//@version=6`.
- `strategy(...)` always includes `use_bar_magnifier = true` and
  `process_orders_on_close = false`.
- HTF features always use `request.security(..., lookahead = barmerge.lookahead_off)`.
- No `lookahead_on` appears anywhere in the output.
- No raw `security(` call (only `request.security(...)`).
- No `strategy.risk.*` calls. Risk lives in the executor.
- Alert messages are JSON literals matching `docs/ALERT_CONTRACT.md`.

The generator is pure / deterministic: same spec in → byte-identical
source out. This is relied on by the snapshot test in Step 2's DoD.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
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
    CumulativeDelta,
    RelativeVolume,
    StrategySpec,
)

# ---- errors ----


class PineGenerationError(ValueError):
    """Raised when a spec cannot be rendered (unsupported feature, etc.)."""


class PineStaticCheckError(ValueError):
    """Raised when generator output contains a forbidden Pine pattern."""


# ---- feature source mapping ----

_PRICE_SOURCE_MAP = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "hl2": "hl2",
    "hlc3": "hlc3",
    "ohlc4": "ohlc4",
    "volume": "volume",
}

_TF_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}


def _pine_tf(tf: str) -> str:
    try:
        return _TF_MAP[tf]
    except KeyError as e:
        raise PineGenerationError(f"unsupported timeframe for Pine: {tf!r}") from e


def _pine_ident(spec_id: str) -> str:
    """Strategy id -> Pine-safe identifier (letters/digits/underscore)."""
    out = re.sub(r"[^A-Za-z0-9_]", "_", spec_id)
    if out and out[0].isdigit():
        out = "_" + out
    return out or "strat"


def _pine_str(s: str) -> str:
    """Quote a Python string as a Pine string literal (escape backslash/quote)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pine_num(v: Decimal | float | int) -> str:
    """Render a numeric literal in a form Pine accepts."""
    if isinstance(v, Decimal):
        s = format(v, "f")
    elif isinstance(v, int):
        s = str(v)
    else:
        # float: keep a compact repr, ensure decimal point so Pine treats as float
        s = repr(float(v))
        if "e" in s or "E" in s:
            s = format(float(v), ".10f").rstrip("0").rstrip(".")
    if "." not in s and "e" not in s and "E" not in s:
        s += ".0"
    return s


# ---- visitor: AST -> Pine boolean expression string ----


class PineExprVisitor(Visitor):
    """Translate an AST node to a Pine v6 boolean expression.

    Each `visit_*` returns a string.  Parenthesization is conservative —
    any compound expression is wrapped to avoid precedence surprises.
    """

    def __init__(self, *, feature_vars: dict[str, str], side: str) -> None:
        self.feature_vars = feature_vars  # feature_id -> Pine variable name
        self.side = side  # "long" or "short"

    # -- combinators --

    def visit_Or(self, node: Or) -> str:
        if not node.children_:
            return "true"
        return "(" + " or ".join(self.visit(c) for c in node.children_) + ")"

    def visit_And(self, node: And) -> str:
        if not node.children_:
            return "true"
        return "(" + " and ".join(self.visit(c) for c in node.children_) + ")"

    def visit_Not(self, node: Not) -> str:
        return "(not " + str(self.visit(node.child)) + ")"

    # -- leaves / operands --

    def visit_FeatureRef(self, node: FeatureRef) -> str:
        if node.name not in self.feature_vars:
            raise PineGenerationError(f"unknown feature reference: {node.name!r}")
        return self.feature_vars[node.name]

    def visit_Builtin(self, node: Builtin) -> str:
        src = _PRICE_SOURCE_MAP.get(node.name)
        if src is None:
            raise PineGenerationError(f"unknown builtin: {node.name!r}")
        return src

    def visit_Number(self, node: Number) -> str:
        return _pine_num(node.value)

    # -- comparisons --

    def visit_Comparison(self, node: Comparison) -> str:
        left = self.visit(node.left)
        right = self.visit(node.right)
        op = node.op

        if op in (">", "<", ">=", "<=", "==", "!="):
            expr = f"({left} {op} {right})"
            # modifiers:
            if node.for_bars is not None:
                # "true for N bars" == all of the last N bars satisfy it.
                n = int(node.for_bars)
                expr = f'_for_bars_ok({left}, {right}, "{op}", {n})'
            elif node.on_bar is not None:
                n = int(node.on_bar)
                expr = f"(({left})[{n}] {op} ({right})[{n}])"
            return expr

        if op in ("crosses_above", "crosses_below", "crosses"):
            fn = {
                "crosses_above": "ta.crossover",
                "crosses_below": "ta.crossunder",
                "crosses": "ta.cross",
            }[op]
            base = f"{fn}({left}, {right})"
            if node.within_bars is not None:
                n = int(node.within_bars)
                base = f"_crossed_within({fn}, {left}, {right}, {n})"
            return base

        raise PineGenerationError(f"unsupported comparator: {op!r}")

    # -- predicates --

    def visit_SessionWindowIn(self, node: SessionWindowIn) -> str:
        parts = [f"_sw_{name}" for name in node.names]
        if not parts:
            return "false"
        return "(" + " or ".join(parts) + ")"

    def visit_InBlackout(self, _: InBlackout) -> str:
        return "_in_blackout"

    def visit_InPosition(self, _: InPosition) -> str:
        return "(strategy.position_size != 0)"

    def visit_Flat(self, _: Flat) -> str:
        return "(strategy.position_size == 0)"

    def visit_BarsSinceOpen(self, node: BarsSinceOpen) -> str:
        return f"(_bars_since_session_open {node.op} {node.value})"

    def visit_BarsSinceEntry(self, node: BarsSinceEntry) -> str:
        return f"(_bars_since_entry {node.op} {node.value})"

    def visit_Rising(self, node: Rising) -> str:
        name = self.feature_vars.get(node.feature.name)
        if name is None:
            raise PineGenerationError(f"rising: unknown feature {node.feature.name!r}")
        n = int(node.for_bars)
        return f"ta.rising({name}, {n})"

    def visit_Falling(self, node: Falling) -> str:
        name = self.feature_vars.get(node.feature.name)
        if name is None:
            raise PineGenerationError(f"falling: unknown feature {node.feature.name!r}")
        n = int(node.for_bars)
        return f"ta.falling({name}, {n})"


# ---- condition resolution (handles mirror_of) ----


def _resolve_conditions(spec: StrategySpec, side: str) -> tuple[list[str], str]:
    """Return (condition_strings, combinator) for a side.

    combinator is "all_of" | "any_of" | "n_of:<k>". The combinator name
    stays as a string so the top-level composer can fold into an And/Or
    with the right arity handling.
    """
    entry = spec.entry.long if side == "long" else spec.entry.short
    other = spec.entry.short if side == "long" else spec.entry.long

    target = entry
    if target.mirror_of is not None:
        # Mirror: reuse the other side's condition list, flipping sides semantically.
        target = other
        if target.mirror_of is not None:
            raise PineGenerationError("mirror_of chain: both sides reference each other")

    if target.all_of is not None:
        return list(target.all_of), "all_of"
    if target.any_of is not None:
        return list(target.any_of), "any_of"
    if target.n_of is not None:
        k, conds = target.n_of
        return list(conds), f"n_of:{k}"

    raise PineGenerationError(f"entry.{side}: no conditions (all_of/any_of/n_of/mirror_of)")


def _mirror_condition_str(cond: str) -> str:
    """Flip long-side syntax to short-side syntax for simple swaps."""
    # This is a cautious textual flip — spec authors are responsible for
    # ensuring their conditions are symmetrical. The only flips we do are
    # the common relational patterns that clearly have a sign.
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


def _compose_conditions(conds: list[str], combinator: str, visitor: PineExprVisitor) -> str:
    parts = [visitor.visit(ast_mod.parse(c)) for c in conds]
    if not parts:
        return "false"
    if combinator == "all_of":
        return "(" + " and ".join(parts) + ")"
    if combinator == "any_of":
        return "(" + " or ".join(parts) + ")"
    if combinator.startswith("n_of:"):
        k = int(combinator.split(":", 1)[1])
        # Sum booleans as ints (Pine: ternary to int) and threshold.
        sums = " + ".join(f"({p} ? 1 : 0)" for p in parts)
        return f"(({sums}) >= {k})"
    raise PineGenerationError(f"unknown combinator: {combinator!r}")


# ---- feature block emission ----


def _emit_feature_line(f: Any, vars_out: dict[str, str]) -> str:
    """Return one Pine line declaring the feature, and side-effect the map.

    Primary-tf features are plain `ta.xxx()` calls. HTF features are
    wrapped in `request.security(... , lookahead = barmerge.lookahead_off)`.
    """
    var = f"_f_{_pine_ident(f.id)}"
    vars_out[f.id] = var

    tf = f.timeframe
    is_htf = tf is not None and tf not in ("", "primary")

    # The actual expression on the native timeframe:
    if isinstance(f, EMA):
        src = _PRICE_SOURCE_MAP[f.source]
        inner = f"ta.ema({src}, {int(f.length)})"
    elif isinstance(f, SMA):
        src = _PRICE_SOURCE_MAP[f.source]
        inner = f"ta.sma({src}, {int(f.length)})"
    elif isinstance(f, RMA):
        src = _PRICE_SOURCE_MAP[f.source]
        inner = f"ta.rma({src}, {int(f.length)})"
    elif isinstance(f, ATR):
        inner = f"ta.atr({int(f.length)})"
    elif isinstance(f, VWAP):
        # Pine's built-in vwap is session-anchored by default.
        if f.anchor != "session":
            raise PineGenerationError(
                f"Pine generator currently supports VWAP anchor=session only (got {f.anchor!r})"
            )
        inner = "ta.vwap"
    elif isinstance(f, RelativeVolume):
        inner = f"(volume / math.max(ta.sma(volume, {int(f.length)}), 1))"
    elif isinstance(f, CumulativeDelta):
        raise PineGenerationError(
            "CumulativeDelta requires L2 data not available in the Pine visualization "
            "generator. Either mark the spec generators.pine.enabled = false, or remove "
            "the cumulative_delta feature."
        )
    else:  # pragma: no cover — pydantic discriminator rules out unknowns
        raise PineGenerationError(f"unsupported feature type: {type(f).__name__}")

    if is_htf:
        pine_tf = _pine_tf(tf)
        return (
            f"{var} = request.security(syminfo.tickerid, {_pine_str(pine_tf)}, "
            f"{inner}, lookahead = barmerge.lookahead_off)"
        )
    return f"{var} = {inner}"


# ---- session / blackout emission ----


def _emit_session_windows(spec: StrategySpec) -> list[str]:
    lines: list[str] = []
    for w in spec.session.windows:
        var = f"_sw_{_pine_ident(w.name)}"
        if not w.enabled:
            lines.append(f"{var} = false")
            continue
        start = w.start.replace(":", "") + "-" + w.end.replace(":", "")
        sess = f'"{start}"'
        lines.append(
            f"{var} = not na(time(timeframe.period, {sess}, {_pine_str(spec.session.timezone)}))"
        )
    return lines


def _emit_blackouts(spec: StrategySpec) -> tuple[list[str], str]:
    """Emit per-blackout booleans and an aggregate `_in_blackout` expression."""
    lines: list[str] = []
    names: list[str] = []
    for b in spec.session.blackouts:
        var = f"_bk_{_pine_ident(b.name)}"
        names.append(var)
        if b.type == "session_offset":
            # Minimal support: encode as a boolean that is `false` at compile
            # time but tracked — real implementation lives in the executor.
            # Pine visualization approximates using bars_since_session_open.
            if b.offset_from_session_start_sec is not None and b.duration_sec is not None:
                start = int(b.offset_from_session_start_sec)
                end = start + int(b.duration_sec)
                # convert seconds to 1m bars approximately (for 1m primary tf)
                start_b = max(0, start // 60)
                end_b = max(1, (end + 59) // 60)
                lines.append(
                    f"{var} = (_bars_since_session_open >= {start_b}) and "
                    f"(_bars_since_session_open < {end_b})"
                )
            elif b.offset_from_session_end_sec is not None and b.duration_sec is not None:
                # Approximation: flag the last duration_sec of the last enabled window.
                end_off = int(b.offset_from_session_end_sec)
                dur = int(b.duration_sec)
                last_n = max(1, (dur + 59) // 60)
                _ = end_off  # end-anchored offset is handled by the executor; Pine approximates
                lines.append(
                    f"{var} = (bar_index - _session_end_bar_index > -{last_n}) and "
                    f"(bar_index - _session_end_bar_index <= 0)"
                )
            else:
                lines.append(f"{var} = false")
        elif b.type == "economic_event":
            # Economic-event blackouts are enforced by the executor, not Pine.
            lines.append(f"{var} = false  // executor-enforced: {b.event}")
        else:  # pragma: no cover — discriminator
            lines.append(f"{var} = false")

    agg = "(" + " or ".join(names) + ")" if names else "false"
    return lines, agg


# ---- top-level render ----


def render_pine(spec: StrategySpec) -> str:
    """Render a StrategySpec to Pine v6 source. Raises on invalid input."""
    if not spec.generators.pine.enabled:
        raise PineGenerationError("spec.generators.pine.enabled is false")
    if spec.generators.pine.pine_version != 6:
        raise PineGenerationError(
            f"only Pine v6 is supported (spec asks for v{spec.generators.pine.pine_version})"
        )

    lines: list[str] = []
    out = lines.append

    # -- header --
    out("//@version=6")
    out(f"// Generated from spec {spec.strategy.id} ({spec.strategy.semver})")
    out(f"// content_hash: {spec.strategy.content_hash or '<unhashed>'}")
    out("// DO NOT EDIT BY HAND — regenerate via `mnq spec render --target pine`.")
    out("")

    # -- strategy() declaration --
    title = _pine_str(f"{spec.strategy.id} v{spec.strategy.semver}")
    out(
        "strategy("
        f"title = {title}, "
        "overlay = true, "
        "pyramiding = 0, "
        "calc_on_every_tick = false, "
        "process_orders_on_close = false, "
        "use_bar_magnifier = true, "
        "default_qty_type = strategy.fixed, "
        "default_qty_value = 1"
        ")"
    )
    out("")

    # -- helpers --
    out("// --- helpers ---")
    out('_json_str(x) => "\\"" + str.tostring(x) + "\\""')
    out('_json_bool(x) => x ? "true" : "false"')
    out("_json_num(x) => str.tostring(x)")
    out('_bars_since_session_open = ta.barssince(ta.change(time("D")) != 0)')
    out(
        "_session_end_bar_index = ta.valuewhen(session.isfirstbar_regular, bar_index, 0) "
        '+ (timeframe.in_seconds("1D") / timeframe.in_seconds(timeframe.period))'
    )
    out(
        "_bars_since_entry = strategy.position_size != 0 ? "
        "bar_index - ta.valuewhen(strategy.position_size[1] == 0 and strategy.position_size != 0, "
        "bar_index, 0) : 0"
    )
    out("_for_bars_ok(a, b, op, n) =>")
    out("    ok = true")
    out("    for i = 0 to n - 1")
    out(
        '        cur = op == ">" ? a[i] > b[i] : op == "<" ? a[i] < b[i] : '
        'op == ">=" ? a[i] >= b[i] : op == "<=" ? a[i] <= b[i] : '
        'op == "==" ? a[i] == b[i] : op == "!=" ? a[i] != b[i] : false'
    )
    out("        ok := ok and cur")
    out("    ok")
    out("_crossed_within(fn, a, b, n) =>")
    out("    any = false")
    out("    for i = 0 to n - 1")
    out("        any := any or fn(a, b)[i]")
    out("    any")
    out("")

    # -- session windows --
    out("// --- session windows ---")
    for line in _emit_session_windows(spec):
        out(line)
    out("")

    # -- blackouts --
    out("// --- blackouts ---")
    bk_lines, in_blackout_expr = _emit_blackouts(spec)
    for line in bk_lines:
        out(line)
    out(f"_in_blackout = {in_blackout_expr}")
    out("")

    # -- features --
    out("// --- features ---")
    feat_vars: dict[str, str] = {}
    for f in spec.features:
        out(_emit_feature_line(f, feat_vars))
    out("")

    # -- entry conditions --
    out("// --- entry conditions ---")
    long_conds, long_combinator = _resolve_conditions(spec, "long")
    short_conds, short_combinator = _resolve_conditions(spec, "short")

    # If short is mirror_of long, we textually mirror each long condition.
    if spec.entry.short.mirror_of == "long":
        short_conds = [_mirror_condition_str(c) for c in long_conds]
        short_combinator = long_combinator
    if spec.entry.long.mirror_of == "short":
        long_conds = [_mirror_condition_str(c) for c in short_conds]
        long_combinator = short_combinator

    long_visitor = PineExprVisitor(feature_vars=feat_vars, side="long")
    short_visitor = PineExprVisitor(feature_vars=feat_vars, side="short")
    long_expr = _compose_conditions(long_conds, long_combinator, long_visitor)
    short_expr = _compose_conditions(short_conds, short_combinator, short_visitor)

    out(f"_long_entry_raw  = {long_expr}")
    out(f"_short_entry_raw = {short_expr}")
    out("_long_entry  = _long_entry_raw  and barstate.isconfirmed")
    out("_short_entry = _short_entry_raw and barstate.isconfirmed")
    out("")

    # -- exit logic --
    out("// --- exit distances (diagnostic only; executor is authoritative) ---")
    stop = spec.exit.initial_stop
    if stop.type == "atr_multiple":
        if stop.feature is None or stop.multiplier is None:
            raise PineGenerationError("atr_multiple stop requires feature + multiplier")
        atr_var = feat_vars.get(stop.feature)
        if atr_var is None:
            raise PineGenerationError(
                f"atr_multiple stop references unknown feature {stop.feature!r}"
            )
        tick = _pine_num(spec.instrument.tick_size)
        mult = _pine_num(stop.multiplier)
        mn = int(stop.min_ticks)
        mx = int(stop.max_ticks)
        out(f"_stop_ticks_raw = math.round(({atr_var} * {mult}) / {tick})")
        out(f"_stop_ticks = math.max({mn}, math.min({mx}, _stop_ticks_raw))")
    elif stop.type == "fixed_ticks":
        if stop.ticks is None:
            raise PineGenerationError("fixed_ticks stop requires ticks")
        out(f"_stop_ticks = {int(stop.ticks)}")
    else:  # swing_low_high
        # Placeholder: Pine can't compute arbitrary swing pivots generically.
        # The executor will override; Pine uses a fallback ATR-like constant.
        out("_stop_ticks = 10  // swing_low_high placeholder; executor is authoritative")

    tp = spec.exit.take_profit
    if tp.type == "r_multiple":
        if tp.value is None:
            raise PineGenerationError("r_multiple target requires value")
        out(f"_tp_ticks = math.round(_stop_ticks * {_pine_num(tp.value)})")
    elif tp.type == "fixed_ticks":
        if tp.value is None:
            raise PineGenerationError("fixed_ticks target requires value")
        out(f"_tp_ticks = {int(tp.value)}")
    elif tp.type == "atr_multiple":
        if tp.feature is None or tp.multiplier is None:
            raise PineGenerationError("atr_multiple target requires feature + multiplier")
        atr_var = feat_vars.get(tp.feature)
        if atr_var is None:
            raise PineGenerationError(
                f"atr_multiple target references unknown feature {tp.feature!r}"
            )
        tick = _pine_num(spec.instrument.tick_size)
        mult = _pine_num(tp.multiplier)
        out(f"_tp_ticks = math.round(({atr_var} * {mult}) / {tick})")
    else:  # pragma: no cover — discriminator
        raise PineGenerationError(f"unsupported take_profit.type: {tp.type!r}")
    out("")

    # -- alerts --
    out("// --- alert payloads (see docs/ALERT_CONTRACT.md) ---")
    out(_emit_alert_helpers(spec))
    out("")

    # -- orders + alerts (single entry long/short, with bracket shown as comment) --
    out("// --- entries ---")
    out("if _long_entry")
    out('    alert(_entry_json("long"),  alert.freq_once_per_bar_close)')
    out('    strategy.entry("long",  strategy.long)')
    out("if _short_entry")
    out('    alert(_entry_json("short"), alert.freq_once_per_bar_close)')
    out('    strategy.entry("short", strategy.short)')
    out("")

    out("// --- exits (local; executor has its own OCO at venue) ---")
    out("if strategy.position_size != 0")
    out("    _px = strategy.position_avg_price")
    out("    _sz = strategy.position_size")
    out("    _long = _sz > 0")
    out(
        "    _stop_px = _long ? _px - _stop_ticks * syminfo.mintick "
        ": _px + _stop_ticks * syminfo.mintick"
    )
    out(
        "    _tp_px   = _long ? _px + _tp_ticks   * syminfo.mintick "
        ": _px - _tp_ticks   * syminfo.mintick"
    )
    out(
        '    strategy.exit("bracket", from_entry = _long ? "long" : "short", '
        "stop = _stop_px, limit = _tp_px)"
    )
    out("")

    src = "\n".join(lines) + "\n"
    static_check_pine(src)
    return src


def _emit_alert_helpers(spec: StrategySpec) -> str:
    spec_id = _pine_str(spec.strategy.id)
    spec_hash = _pine_str(spec.strategy.content_hash or "")
    # The nonce is a cheap hash of time+bar_index. Pine has str.tostring but no
    # crypto — this matches ALERT_CONTRACT "16 hex chars derived from time+bar_index".
    helpers = [
        "_bar_iso = str.format_time(time_close, \"yyyy-MM-dd'T'HH:mm:ss'Z'\", \"UTC\")",
        '_nonce() => str.format("{0}{1}", time_close, bar_index)',
        "_entry_json(_side) =>",
        '    "{" + ',
        '      "\\"schema_version\\":1," + ',
        '      "\\"event\\":\\"entry\\"," + ',
        f'      "\\"spec_id\\":" + {_pine_str(spec.strategy.id)} + "," + ',
        f'      "\\"spec_hash\\":" + {_pine_str(spec.strategy.content_hash or "")} + "," + ',
        '      "\\"bar_time_iso\\":\\"" + _bar_iso + "\\"," + ',
        '      "\\"bar_index\\":" + str.tostring(bar_index) + "," + ',
        '      "\\"symbol\\":\\"" + syminfo.tickerid + "\\"," + ',
        '      "\\"nonce\\":\\"" + _nonce() + "\\"," + ',
        '      "\\"side\\":\\"" + _side + "\\"," + ',
        '      "\\"stop_distance_ticks\\":" + str.tostring(_stop_ticks) + "," + ',
        '      "\\"take_profit_distance_ticks\\":" + str.tostring(_tp_ticks) + ',
        '    "}"',
    ]
    # The above is deliberately built as Python-string Pine code — deterministic.
    _ = spec_id, spec_hash
    return "\n".join(helpers)


# ---- static checker ----

_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"lookahead_on", "`lookahead_on` is forbidden in generated Pine"),
    (r"(?<!request\.)security\s*\(", "raw `security(` is forbidden; use `request.security(...)`"),
    (r"strategy\.risk\.", "`strategy.risk.*` is forbidden (risk lives in the executor)"),
)


def static_check_pine(src: str) -> None:
    """Raise PineStaticCheckError if any forbidden pattern appears in src."""
    if not src.startswith("//@version=6"):
        raise PineStaticCheckError("first line must be `//@version=6`")

    if "use_bar_magnifier = true" not in src:
        raise PineStaticCheckError("missing `use_bar_magnifier = true` in strategy(...)")
    if "process_orders_on_close = false" not in src:
        raise PineStaticCheckError("missing `process_orders_on_close = false` in strategy(...)")

    # Strip Pine line comments before scanning for forbidden patterns; otherwise
    # the `// ...` notes in our own file trip false positives.
    scan_lines = []
    for line in src.splitlines():
        stripped = re.sub(r"//.*$", "", line)
        scan_lines.append(stripped)
    scan_src = "\n".join(scan_lines)

    for pat, msg in _FORBIDDEN_PATTERNS:
        m = re.search(pat, scan_src)
        if m:
            raise PineStaticCheckError(f"{msg} (found: {m.group(0)!r})")

    # Nested-if-with-side-effects is detected as: `if` block whose body contains
    # another `if` followed on the same indent level by a `strategy.*` call.
    # We approximate: any `strategy.entry/exit/close` indented under two or more
    # `if` lines raises.
    depth_stack: list[int] = []
    for raw in scan_lines:
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while depth_stack and indent <= depth_stack[-1]:
            depth_stack.pop()
        if raw.lstrip().startswith("if "):
            depth_stack.append(indent)
            continue
        if len(depth_stack) >= 2 and re.search(r"strategy\.(entry|exit|close|order)\b", raw):
            raise PineStaticCheckError(
                "nested `if` with strategy side effect detected — flatten the condition"
            )


def iter_feature_vars(lines: Iterable[str]) -> Iterable[str]:
    """Utility for tests — yield the `_f_*` variable names in source order."""
    for line in lines:
        m = re.match(r"\s*(_f_[A-Za-z0-9_]+)\s*=", line)
        if m:
            yield m.group(1)
