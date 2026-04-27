"""Level-1 unit tests for mnq.venues.tradovate.ws (pure protocol layer).

The async connection manager is exercised in the level-5 integration test;
here we cover only the deterministic pure functions.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from mnq.venues.tradovate.ws import (
    FrameType,
    WsFrame,
    build_authorize,
    build_request,
    is_authorize_ok,
    parse_frame,
)


class TestParseFrame:
    def test_open(self) -> None:
        f = parse_frame("o")
        assert f.type is FrameType.OPEN
        assert f.payload is None

    def test_heartbeat(self) -> None:
        f = parse_frame("h")
        assert f.type is FrameType.HEARTBEAT
        assert f.payload is None

    def test_array_with_auth_ack(self) -> None:
        f = parse_frame('a[{"s":200,"i":1}]')
        assert f.type is FrameType.ARRAY
        assert f.payload == [{"s": 200, "i": 1}]

    def test_array_empty_payload(self) -> None:
        f = parse_frame("a")  # degenerate, shouldn't happen but shouldn't crash
        assert f.type is FrameType.ARRAY
        assert f.payload == []

    def test_close(self) -> None:
        f = parse_frame('c[1000, "normal closure"]')
        assert f.type is FrameType.CLOSE
        assert f.payload == [1000, "normal closure"]

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_frame("")

    def test_unknown_prefix_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_frame("x[]")

    def test_malformed_array_json_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_frame("a{not json")


class TestBuildRequest:
    def test_newline_delimited_four_segments(self) -> None:
        req = build_request("authorize", 1, body="TOK")
        parts = req.split("\n")
        assert parts == ["authorize", "1", "", "TOK"]

    def test_query_is_empty_line_when_unused(self) -> None:
        req = build_request("op", 5)
        assert req == "op\n5\n\n"

    def test_with_query_and_body(self) -> None:
        req = build_request("md/subscribeQuote", 7, query="", body='{"symbol":"MNQM6"}')
        assert req == 'md/subscribeQuote\n7\n\n{"symbol":"MNQM6"}'


class TestBuildAuthorize:
    def test_accepts_string_token(self) -> None:
        assert build_authorize("ABC") == "authorize\n1\n\nABC"

    def test_accepts_token_object(self) -> None:
        from datetime import datetime

        from mnq.venues.tradovate.auth import Token

        tok = Token(
            access_token="XYZ",
            expires_at=datetime(2026, 4, 14, 13, 30, tzinfo=UTC),
            issued_at=datetime(2026, 4, 14, 12, 0, tzinfo=UTC),
            user_id=1,
            user_name="u",
            has_live=False,
            user_status="Active",
        )
        assert build_authorize(tok) == "authorize\n1\n\nXYZ"


class TestIsAuthorizeOk:
    def test_true_on_200_with_matching_id(self) -> None:
        frame = WsFrame(type=FrameType.ARRAY, payload=[{"s": 200, "i": 1}])
        assert is_authorize_ok(frame, expected_id=1)

    def test_false_on_wrong_id(self) -> None:
        frame = WsFrame(type=FrameType.ARRAY, payload=[{"s": 200, "i": 2}])
        assert not is_authorize_ok(frame, expected_id=1)

    def test_false_on_non_200_status(self) -> None:
        frame = WsFrame(type=FrameType.ARRAY, payload=[{"s": 401, "i": 1}])
        assert not is_authorize_ok(frame, expected_id=1)

    def test_false_on_heartbeat(self) -> None:
        frame = WsFrame(type=FrameType.HEARTBEAT)
        assert not is_authorize_ok(frame)

    def test_true_when_ack_is_one_of_several_events(self) -> None:
        frame = WsFrame(
            type=FrameType.ARRAY,
            payload=[
                {"e": "props", "d": {"x": 1}},
                {"s": 200, "i": 1},
            ],
        )
        assert is_authorize_ok(frame)
