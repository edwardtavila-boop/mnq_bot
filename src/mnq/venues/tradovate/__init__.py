"""[REAL] Tradovate venue client — auth, REST, WebSocket.

Public surface the executor and CLI use:

    from mnq.venues.tradovate import (
        TradovateCreds,
        Token,
        TradovateAuthClient,
        TradovateRestClient,
        TradovateWsClient,
        Environment,
        hosts_for,
        AuthError, SessionLimitError, InvalidCredentialsError,
        OrderRejectedError,
    )

Nothing else is considered a stable contract. See `docs/TRADOVATE_NOTES.md`
for the protocol details this module encodes.
"""
from mnq.venues.tradovate.auth import (
    AuthError,
    InvalidCredentialsError,
    SessionLimitError,
    Token,
    TradovateAuthClient,
    TradovateCreds,
    parse_access_token_response,
)
from mnq.venues.tradovate.config import Environment, Hosts, hosts_for
from mnq.venues.tradovate.rest import (
    AccountInfo,
    BracketParams,
    OrderRejectedError,
    TradovateRestClient,
)
from mnq.venues.tradovate.ws import (
    FrameType,
    TradovateWsClient,
    WsDisconnectError,
    WsFrame,
    parse_frame,
)

__all__ = [
    "AccountInfo",
    "AuthError",
    "BracketParams",
    "Environment",
    "FrameType",
    "Hosts",
    "InvalidCredentialsError",
    "OrderRejectedError",
    "SessionLimitError",
    "Token",
    "TradovateAuthClient",
    "TradovateCreds",
    "TradovateRestClient",
    "TradovateWsClient",
    "WsDisconnectError",
    "WsFrame",
    "hosts_for",
    "parse_access_token_response",
    "parse_frame",
]
