"""[REAL] Condition grammar: parse strings to AST, evaluate via visitors.

Grammar (EBNF-ish):

    condition   := or_expr
    or_expr     := and_expr ("or" and_expr)*
    and_expr    := not_expr ("and" not_expr)*
    not_expr    := "not" not_expr | atom
    atom        := comparison | predicate | "(" condition ")"

    comparison  := operand comparator operand modifier?
    comparator  := ">" | "<" | ">=" | "<=" | "==" | "!="
                 | "crosses_above" | "crosses_below" | "crosses"
    modifier    := "within_bars" INT | "for_bars" INT | "on_bar" INT

    operand     := feature_ref | builtin | NUMBER
    feature_ref := "feature:" IDENT
    builtin     := "close" | "open" | "high" | "low" | "volume"
                 | "hl2" | "hlc3" | "ohlc4"

    predicate   := "session_window" "in" "[" IDENT ("," IDENT)* "]"
                 | "in_blackout"
                 | "bars_since_session_open" comparator INT
                 | "in_position" | "flat"
                 | "bars_since_entry" comparator INT
                 | "rising" feature_ref ("for_bars" INT)?
                 | "falling" feature_ref ("for_bars" INT)?

This module provides:
    parse(s) -> Node       — string to AST
    Node                    — base of the AST hierarchy (frozen dataclasses)
    Visitor                 — base visitor; pine and python_exec generators subclass

The agent's mutation operators (in spec/mutations.py) operate on Nodes,
not strings, ensuring every produced spec parses by construction.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

# ---- AST nodes ----

class Node:
    """Base AST node."""
    def children(self) -> Iterator[Node]:
        return iter(())


@dataclass(frozen=True)
class Or(Node):
    children_: tuple[Node, ...]
    def children(self) -> Iterator[Node]:
        return iter(self.children_)


@dataclass(frozen=True)
class And(Node):
    children_: tuple[Node, ...]
    def children(self) -> Iterator[Node]:
        return iter(self.children_)


@dataclass(frozen=True)
class Not(Node):
    child: Node
    def children(self) -> Iterator[Node]:
        return iter((self.child,))


@dataclass(frozen=True)
class FeatureRef(Node):
    name: str


@dataclass(frozen=True)
class Builtin(Node):
    name: str   # "close", "high", etc.


@dataclass(frozen=True)
class Number(Node):
    value: float


@dataclass(frozen=True)
class Comparison(Node):
    left: Node
    op: str               # ">", "<", "crosses_above", etc.
    right: Node
    within_bars: int | None = None
    for_bars: int | None = None
    on_bar: int | None = None


@dataclass(frozen=True)
class SessionWindowIn(Node):
    names: tuple[str, ...]


@dataclass(frozen=True)
class InBlackout(Node):
    pass


@dataclass(frozen=True)
class BarsSinceOpen(Node):
    op: str
    value: int


@dataclass(frozen=True)
class InPosition(Node):
    pass


@dataclass(frozen=True)
class Flat(Node):
    pass


@dataclass(frozen=True)
class BarsSinceEntry(Node):
    op: str
    value: int


@dataclass(frozen=True)
class Rising(Node):
    feature: FeatureRef
    for_bars: int = 1


@dataclass(frozen=True)
class Falling(Node):
    feature: FeatureRef
    for_bars: int = 1


# ---- parser ----

# Tokens: keywords, comparators, identifiers, numbers, punctuation.
COMPARATORS: tuple[str, ...] = (
    ">=", "<=", "==", "!=", ">", "<",
    "crosses_above", "crosses_below", "crosses",
)
BUILTINS: frozenset[str] = frozenset(("open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4"))


class ParseError(ValueError):
    pass


def parse(s: str) -> Node:
    """Parse a condition string into an AST."""
    tokens = _tokenize(s)
    parser = _Parser(tokens)
    node = parser.parse_or()
    if parser.pos != len(tokens):
        raise ParseError(f"trailing tokens: {tokens[parser.pos:]!r}")
    return node


def _tokenize(s: str) -> list[str]:
    """Whitespace-separated, with bracket/paren/comma split."""
    # Insert spaces around special characters
    for ch in "()[],":
        s = s.replace(ch, f" {ch} ")
    # Multi-char comparators are pre-split friendly; ensure they tokenize as one
    out: list[str] = []
    for tok in s.split():
        out.append(tok)
    return out


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self, k: int = 0) -> str | None:
        i = self.pos + k
        return self.tokens[i] if i < len(self.tokens) else None

    def eat(self, expected: str | None = None) -> str:
        if self.pos >= len(self.tokens):
            raise ParseError(f"expected {expected!r}, got EOF")
        tok = self.tokens[self.pos]
        if expected is not None and tok != expected:
            raise ParseError(f"expected {expected!r}, got {tok!r}")
        self.pos += 1
        return tok

    def eat_int(self) -> int:
        """Eat the next token and parse it as an int, raising ParseError on
        failure. Use this anywhere the grammar requires an integer literal."""
        tok = self.eat()
        try:
            return int(tok)
        except ValueError as e:
            raise ParseError(f"expected integer literal, got {tok!r}") from e

    def parse_or(self) -> Node:
        nodes = [self.parse_and()]
        while self.peek() == "or":
            self.eat("or")
            nodes.append(self.parse_and())
        return Or(tuple(nodes)) if len(nodes) > 1 else nodes[0]

    def parse_and(self) -> Node:
        nodes = [self.parse_not()]
        while self.peek() == "and":
            self.eat("and")
            nodes.append(self.parse_not())
        return And(tuple(nodes)) if len(nodes) > 1 else nodes[0]

    def parse_not(self) -> Node:
        if self.peek() == "not":
            self.eat("not")
            return Not(self.parse_not())
        return self.parse_atom()

    def parse_atom(self) -> Node:
        tok = self.peek()
        if tok == "(":
            self.eat("(")
            node = self.parse_or()
            self.eat(")")
            return node
        if tok == "session_window":
            return self._parse_session_window_in()
        if tok == "in_blackout":
            self.eat()
            return InBlackout()
        if tok == "in_position":
            self.eat()
            return InPosition()
        if tok == "flat":
            self.eat()
            return Flat()
        if tok == "bars_since_session_open":
            return self._parse_bars_since(BarsSinceOpen)
        if tok == "bars_since_entry":
            return self._parse_bars_since(BarsSinceEntry)
        if tok == "rising":
            return self._parse_rising_falling(Rising)
        if tok == "falling":
            return self._parse_rising_falling(Falling)
        return self._parse_comparison()

    def _parse_session_window_in(self) -> SessionWindowIn:
        self.eat("session_window")
        self.eat("in")
        self.eat("[")
        names: list[str] = []
        while self.peek() != "]":
            names.append(self.eat())
            if self.peek() == ",":
                self.eat(",")
        self.eat("]")
        return SessionWindowIn(tuple(names))

    def _parse_bars_since(self, cls: type[Node]) -> Node:
        self.eat()  # bars_since_*
        op = self.eat()
        if op not in (">", "<", ">=", "<=", "==", "!="):
            raise ParseError(f"bars_since_*: expected comparator, got {op!r}")
        val = self.eat_int()
        return cls(op=op, value=val)  # type: ignore[call-arg]

    def _parse_rising_falling(self, cls: type[Node]) -> Node:
        self.eat()  # rising | falling
        feat = self._parse_operand()
        if not isinstance(feat, FeatureRef):
            raise ParseError(f"rising/falling expects feature_ref, got {feat!r}")
        for_bars = 1
        if self.peek() == "for_bars":
            self.eat("for_bars")
            for_bars = self.eat_int()
        return cls(feature=feat, for_bars=for_bars)  # type: ignore[call-arg]

    def _parse_operand(self) -> Node:
        tok = self.eat()
        if tok.startswith("feature:"):
            return FeatureRef(name=tok[len("feature:"):])
        if tok in BUILTINS:
            return Builtin(name=tok)
        try:
            return Number(value=float(tok))
        except ValueError as e:
            raise ParseError(f"expected operand, got {tok!r}") from e

    def _parse_comparison(self) -> Node:
        left = self._parse_operand()
        op = self.eat()
        if op not in COMPARATORS:
            raise ParseError(f"expected comparator, got {op!r}")
        right = self._parse_operand()
        within_bars = for_bars = on_bar = None
        if self.peek() == "within_bars":
            self.eat("within_bars")
            within_bars = self.eat_int()
        elif self.peek() == "for_bars":
            self.eat("for_bars")
            for_bars = self.eat_int()
        elif self.peek() == "on_bar":
            self.eat("on_bar")
            on_bar = self.eat_int()
        return Comparison(left, op, right, within_bars=within_bars, for_bars=for_bars, on_bar=on_bar)


# ---- visitor base ----

class Visitor:
    """Override visit_<NodeClass> methods. Default visits children."""

    def visit(self, node: Node) -> Any:
        method = getattr(self, f"visit_{type(node).__name__}", None)
        if method:
            return method(node)
        return self.generic_visit(node)

    def generic_visit(self, node: Node) -> None:
        for c in node.children():
            self.visit(c)
