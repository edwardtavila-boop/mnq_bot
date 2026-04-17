"""[REAL] Tradovate WebSocket client — frame protocol, heartbeat, reconnect.

See `docs/TRADOVATE_NOTES.md` §3.

Split into two layers:

    parse_frame / build_request   — pure string⇄struct functions.
                                    Fully unit-testable with no I/O.
    TradovateWsClient             — async connection manager that uses the
                                    pure layer over a websockets.connect.

Frame prefixes we handle:
    'o'  open           (first server frame; no payload)
    'h'  heartbeat      (server keep-alive; no payload)
    'a'  array          (JSON array of server messages)
    'c'  close          (JSON [code, reason]; server-initiated)

Invariants:
    - Client sends '[]' every ~2.5s.
    - If >7s elapses since the last ANY inbound frame, the socket is
      considered dead regardless of OS state — we force-close and reconnect.
    - After (re)connect, the first thing we send is the plain-text authorize
      request; nothing else is sent until a 200 OK reply is observed.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover — websockets is a hard dep
    websockets = None  # type: ignore[assignment]
    ConnectionClosed = Exception  # type: ignore[misc,assignment]

from mnq.venues.tradovate.auth import Token

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — see TRADOVATE_NOTES §3
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL_S = 2.5         # we send '[]' this often
STALE_CUTOFF_S = 7.0               # >this long without any inbound = dead
CLIENT_HEARTBEAT_FRAME = "[]"
RECONNECT_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0)
HARD_DISCONNECT_AFTER = 3          # N consecutive reconnect failures


# ---------------------------------------------------------------------------
# Frame model
# ---------------------------------------------------------------------------

class FrameType(str, Enum):
    OPEN = "o"
    HEARTBEAT = "h"
    ARRAY = "a"
    CLOSE = "c"

    @classmethod
    def from_prefix(cls, ch: str) -> FrameType:
        for ft in cls:
            if ft.value == ch:
                return ft
        raise ValueError(f"unknown frame prefix: {ch!r}")


@dataclass(frozen=True, slots=True)
class WsFrame:
    """A parsed inbound WS frame."""
    type: FrameType
    payload: Any = None           # list for ARRAY/CLOSE, None for OPEN/HEARTBEAT


class WsDisconnectError(Exception):
    """Raised by the receive loop when the socket is considered dead."""


# ---------------------------------------------------------------------------
# Pure parsers / builders
# ---------------------------------------------------------------------------

def parse_frame(raw: str) -> WsFrame:
    """Parse one raw WS text frame into a WsFrame.

    >>> parse_frame("o").type
    <FrameType.OPEN: 'o'>
    >>> parse_frame("h").type
    <FrameType.HEARTBEAT: 'h'>
    >>> parse_frame('a[{"s":200,"i":1}]').payload
    [{'s': 200, 'i': 1}]
    """
    if not raw:
        raise ValueError("empty frame")
    prefix = raw[0]
    ftype = FrameType.from_prefix(prefix)
    if ftype in (FrameType.OPEN, FrameType.HEARTBEAT):
        return WsFrame(type=ftype)
    rest = raw[1:]
    if not rest:
        # 'a' with no payload is ill-formed but handle gracefully as empty.
        return WsFrame(type=ftype, payload=[])
    try:
        payload = json.loads(rest)
    except json.JSONDecodeError as e:
        raise ValueError(f"malformed JSON after {prefix!r} prefix: {e}") from e
    return WsFrame(type=ftype, payload=payload)


def build_request(operation: str, request_id: int, query: str = "", body: str = "") -> str:
    """Build a Tradovate newline-delimited WS request.

    Format:   operation \\n id \\n query \\n \\n body

    The fourth segment (the blank line between query and body) is required
    even when both are empty.

    >>> build_request("authorize", 1, body="TOK")
    'authorize\\n1\\n\\nTOK'
    >>> build_request("md/subscribeQuote", 2, body='{"symbol":"MNQM6"}')
    'md/subscribeQuote\\n2\\n\\n{"symbol":"MNQM6"}'
    """
    return f"{operation}\n{request_id}\n{query}\n{body}"


def build_authorize(token: Token | str, request_id: int = 1) -> str:
    access = token.access_token if isinstance(token, Token) else token
    return build_request("authorize", request_id, body=access)


def is_authorize_ok(frame: WsFrame, expected_id: int = 1) -> bool:
    """True iff `frame` is an 'a' frame containing {s:200, i:expected_id}."""
    if frame.type is not FrameType.ARRAY:
        return False
    payload = frame.payload or []
    if not isinstance(payload, list):
        return False
    for item in payload:
        if isinstance(item, dict) and item.get("i") == expected_id and item.get("s") == 200:
            return True
    return False


# ---------------------------------------------------------------------------
# Event callback types
# ---------------------------------------------------------------------------

OnMessage = Callable[[WsFrame], Awaitable[None]]
OnStatus = Callable[[str], Awaitable[None]]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

@dataclass
class _WsStats:
    connects: int = 0
    reconnects: int = 0
    frames_in: int = 0
    frames_out: int = 0
    last_frame_in_ts: float = 0.0
    last_frame_out_ts: float = 0.0
    authorized: bool = False
    consecutive_failures: int = 0


class TradovateWsClient:
    """Async WebSocket client for one Tradovate socket (trading or market-data).

    The client manages:
        - connect → receive 'o' → send authorize → expect {s:200} reply
        - 2.5s heartbeat loop
        - 7s stale detection
        - reconnect with exponential backoff
        - hard-disconnect escalation after `HARD_DISCONNECT_AFTER` failures

    It does NOT own subscriptions. Callers re-subscribe in the `on_open`
    callback, which is invoked after every successful authorize.
    """

    def __init__(
        self,
        url: str,
        token_provider: Callable[[], Token],
        *,
        on_message: OnMessage | None = None,
        on_status: OnStatus | None = None,
        on_open: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self._url = url
        self._token_provider = token_provider
        self._on_message = on_message
        self._on_status = on_status
        self._on_open = on_open
        self._clock = clock or asyncio.get_event_loop().time

        self._ws: Any = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._stopping = False
        self._stats = _WsStats()
        self._next_request_id = 2  # 1 is reserved for authorize

    # ---- public api --------------------------------------------------------

    @property
    def stats(self) -> _WsStats:
        return self._stats

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._stats.authorized

    async def run(self) -> None:
        """Connect and run until `stop()` is called. Handles reconnects internally."""
        attempt = 0
        while not self._stopping:
            try:
                await self._connect_once()
                attempt = 0  # successful connect resets backoff
                await self._drive_loops()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._stats.consecutive_failures += 1
                await self._emit_status(f"ws disconnected: {e!r}")
                if self._stats.consecutive_failures >= HARD_DISCONNECT_AFTER:
                    await self._emit_status("ws hard-disconnect threshold reached")
                    raise
                delay = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
                attempt += 1
                await asyncio.sleep(delay)

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        if self._ws is not None:
            with contextlib.suppress(Exception):  # pragma: no cover
                await self._ws.close()

    async def send_request(self, operation: str, query: str = "", body: str = "") -> int:
        """Fire a text-protocol request with an auto-assigned id. Returns the id."""
        if not self._ws:
            raise WsDisconnectError("ws not connected")
        rid = self._next_request_id
        self._next_request_id += 1
        frame = build_request(operation, rid, query=query, body=body)
        await self._send_raw(frame)
        return rid

    # ---- internal ----------------------------------------------------------

    async def _connect_once(self) -> None:
        if websockets is None:  # pragma: no cover
            raise RuntimeError("websockets library not installed")
        self._stats.authorized = False
        self._ws = await websockets.connect(self._url, open_timeout=10, ping_interval=None)
        self._stats.connects += 1
        # Expect 'o' first.
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        self._note_inbound()
        frame = parse_frame(raw)
        if frame.type is not FrameType.OPEN:
            raise WsDisconnectError(f"expected 'o' at connect, got {frame.type}")
        # Authorize.
        token = self._token_provider()
        await self._send_raw(build_authorize(token))
        # Next 'a' frame should be our auth ack.
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            self._note_inbound()
            frame = parse_frame(raw)
            if frame.type is FrameType.HEARTBEAT:
                continue
            if is_authorize_ok(frame, expected_id=1):
                break
            raise WsDisconnectError(f"authorize failed: {frame}")
        self._stats.authorized = True
        self._stats.consecutive_failures = 0
        await self._emit_status("ws authorized")
        if self._on_open:
            await self._on_open()

    async def _drive_loops(self) -> None:
        self._tasks = [
            asyncio.create_task(self._recv_loop(), name="ws-recv"),
            asyncio.create_task(self._heartbeat_loop(), name="ws-hb"),
            asyncio.create_task(self._stale_check_loop(), name="ws-stale"),
        ]
        try:
            done, pending = await asyncio.wait(
                self._tasks, return_when=asyncio.FIRST_EXCEPTION
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc is not None:
                    raise exc
        finally:
            self._tasks = []

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        while True:
            try:
                raw = await self._ws.recv()
            except ConnectionClosed as e:
                raise WsDisconnectError(f"connection closed: {e}") from e
            self._note_inbound()
            frame = parse_frame(raw)
            if frame.type is FrameType.CLOSE:
                raise WsDisconnectError(f"server sent close frame: {frame.payload}")
            if self._on_message:
                await self._on_message(frame)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            await self._send_raw(CLIENT_HEARTBEAT_FRAME)

    async def _stale_check_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            since = self._clock() - self._stats.last_frame_in_ts
            if since > STALE_CUTOFF_S:
                raise WsDisconnectError(f"no inbound frame for {since:.1f}s (>{STALE_CUTOFF_S}s cutoff)")

    async def _send_raw(self, text: str) -> None:
        assert self._ws is not None
        await self._ws.send(text)
        self._stats.frames_out += 1
        self._stats.last_frame_out_ts = self._clock()

    def _note_inbound(self) -> None:
        self._stats.frames_in += 1
        self._stats.last_frame_in_ts = self._clock()

    async def _emit_status(self, msg: str) -> None:
        log.info("tradovate.ws %s", msg)
        if self._on_status:
            await self._on_status(msg)
