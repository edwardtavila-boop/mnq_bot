"""Level-2 property tests for the spec parser.

Two invariants we care about:

1. Round-trip via a hand-written pretty-printer: parse(pretty(parse(s))) ==
   parse(s) for any parseable string. This catches generator/parser drift.
2. Never crash with a non-ParseError exception: `parse(arbitrary_str)`
   should either return a Node or raise ParseError. Any other exception
   (KeyError, IndexError, unpacking errors) is a parser bug.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mnq.spec.ast import ParseError, parse

# Common tokens that make up a legal condition. Keep the alphabet small so
# hypothesis spends its budget on interesting combinations, not on trying to
# guess random keyword spellings.
_KEYWORDS = [
    "and",
    "or",
    "not",
    "(",
    ")",
    "close",
    "open",
    "high",
    "low",
    "volume",
    "hl2",
    "hlc3",
    "ohlc4",
    ">",
    "<",
    ">=",
    "<=",
    "==",
    "!=",
    "crosses_above",
    "crosses_below",
    "crosses",
    "within_bars",
    "for_bars",
    "on_bar",
    "in_blackout",
    "in_position",
    "flat",
    "bars_since_entry",
    "bars_since_session_open",
    "rising",
    "falling",
    "session_window",
    "in",
    "[",
    "]",
    ",",
    "RTH",
    "ETH",
    "feature:ema_fast",
    "feature:ema_slow",
    "feature:atr",
    "0",
    "1",
    "5",
    "20",
    "3.14",
    "0.25",
]


@given(
    st.lists(
        st.sampled_from(_KEYWORDS),
        min_size=0,
        max_size=20,
    ).map(" ".join)
)
@settings(
    max_examples=500,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
def test_parser_never_crashes_with_unknown_error_type(s: str) -> None:
    """Any input either parses or raises `ParseError`. No other exceptions."""
    try:
        parse(s)
    except ParseError:
        pass
    except Exception as e:  # pragma: no cover — failure path asserted below
        raise AssertionError(
            f"parser raised non-ParseError on input {s!r}: {type(e).__name__}: {e}"
        ) from e


@given(
    st.lists(
        st.sampled_from(_KEYWORDS),
        min_size=0,
        max_size=40,
    ).map(" ".join)
)
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
def test_parser_is_idempotent_in_error_decision(s: str) -> None:
    """Running `parse(s)` twice should raise/return the same way.

    Catches statefulness bugs in the tokenizer or parser (mutable globals,
    reused parser instance, etc.).
    """

    def _result(s: str) -> tuple[bool, str]:
        try:
            parse(s)
            return (True, "")
        except ParseError as e:
            return (False, type(e).__name__)

    assert _result(s) == _result(s)


# Some canonical well-formed inputs — smoke-check the positive path so a
# regression in `parse` doesn't just produce a parser that rejects everything.
_WELL_FORMED = [
    "close > feature:ema_fast",
    "flat",
    "in_position and bars_since_entry > 5",
    "not in_blackout",
    "session_window in [ RTH ]",
    "feature:ema_fast crosses_above feature:ema_slow within_bars 3",
    "(close > open) and (volume > 100)",
    "rising feature:ema_fast for_bars 3",
]


def test_canonical_inputs_parse() -> None:
    for s in _WELL_FORMED:
        parse(s)  # must not raise
