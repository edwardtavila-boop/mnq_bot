"""Level-1 unit tests for mnq.venues.tradovate.auth.

Covers:
    - TradovateCreds.from_env missing-vars behavior
    - Token lifetime queries (age, needs_renewal, is_expired)
    - parse_access_token_response happy path + error classification
    - TradovateAuthClient over an httpx.MockTransport (no real network)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mnq.venues.tradovate.auth import (
    ABSOLUTE_MAX_AGE,
    DEFAULT_RENEW_AT,
    AuthError,
    InvalidCredentialsError,
    SessionLimitError,
    Token,
    TokenRenewalError,
    TradovateAuthClient,
    TradovateCreds,
    _parse_iso_utc,
    parse_access_token_response,
)
from mnq.venues.tradovate.config import hosts_for

UTC = UTC


# ---------- TradovateCreds --------------------------------------------------


class TestTradovateCreds:
    def test_from_env_missing_vars_raises(self) -> None:
        with pytest.raises(ValueError) as ei:
            TradovateCreds.from_env({"TV_USERNAME": "u"})
        assert "TV_PASSWORD" in str(ei.value)

    def test_from_env_happy(self) -> None:
        env = {
            "TV_USERNAME": "u",
            "TV_PASSWORD": "p",
            "TV_APP_ID": "a",
            "TV_APP_VERSION": "1",
            "TV_DEVICE_ID": "d",
            "TV_CID": "c",
            "TV_SEC": "s",
        }
        creds = TradovateCreds.from_env(env)
        assert creds.name == "u"
        body = creds.as_request_body()
        assert body["appId"] == "a"
        assert body["cid"] == "c"
        assert body["sec"] == "s"


# ---------- Token -----------------------------------------------------------


def make_token(
    *,
    issued_at: datetime,
    expires_at: datetime | None = None,
    access: str = "TOK",
) -> Token:
    return Token(
        access_token=access,
        expires_at=expires_at or issued_at + timedelta(minutes=90),
        issued_at=issued_at,
        user_id=1,
        user_name="u",
        has_live=False,
        user_status="Active",
    )


class TestToken:
    def test_age_is_now_minus_issued(self) -> None:
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        tok = make_token(issued_at=issued)
        assert tok.age(issued) == timedelta(0)
        assert tok.age(issued + timedelta(minutes=30)) == timedelta(minutes=30)

    def test_needs_renewal_boundary(self) -> None:
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        tok = make_token(issued_at=issued)
        assert not tok.needs_renewal(issued + timedelta(minutes=74))
        assert tok.needs_renewal(issued + DEFAULT_RENEW_AT)
        assert tok.needs_renewal(issued + timedelta(minutes=80))

    def test_custom_threshold(self) -> None:
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        tok = make_token(issued_at=issued)
        assert tok.needs_renewal(
            issued + timedelta(minutes=30),
            threshold=timedelta(minutes=29),
        )

    def test_jitter_key_stability_and_range(self) -> None:
        """Jitter produces a deterministic offset in [-jitter, +jitter]."""
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        tok = make_token(issued_at=issued)
        jitter = timedelta(seconds=60)
        # With the default threshold (75 min), tokens at age 74 min sometimes
        # renew, sometimes don't, depending on jitter_key — but the boundary
        # is stable per key.
        key_a = "host-a/pid-1/2026-04-14T12:00"
        key_b = "host-b/pid-2/2026-04-14T12:00"
        key_c = "host-c/pid-3/2026-04-14T12:00"

        def renew_at(key: str) -> float:
            """Find the exact age in seconds at which the token renews."""
            lo, hi = 74 * 60.0, 76 * 60.0
            for _ in range(40):
                mid = (lo + hi) / 2
                if tok.needs_renewal(
                    issued + timedelta(seconds=mid), jitter_key=key, jitter=jitter
                ):
                    hi = mid
                else:
                    lo = mid
            return hi

        offs = [renew_at(k) for k in (key_a, key_b, key_c)]
        for o in offs:
            # Each is within the 2-minute spread window.
            assert 74 * 60 <= o <= 76 * 60, f"offset {o} out of band"
        # Different keys should produce at least some spread.
        assert max(offs) - min(offs) > 1.0

        # Determinism: same key yields identical result across calls.
        assert renew_at(key_a) == renew_at(key_a)

    def test_is_expired_by_server_clock(self) -> None:
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        expires = issued + timedelta(minutes=60)
        tok = make_token(issued_at=issued, expires_at=expires)
        assert not tok.is_expired(issued + timedelta(minutes=59))
        assert tok.is_expired(expires)
        assert tok.is_expired(expires + timedelta(seconds=1))

    def test_is_expired_by_absolute_max_age(self) -> None:
        """Even if the server says we have time, we bail past ABSOLUTE_MAX_AGE."""
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        # Server claims expiry is far in the future (clock skew scenario):
        expires = issued + timedelta(hours=6)
        tok = make_token(issued_at=issued, expires_at=expires)
        assert not tok.is_expired(issued + timedelta(minutes=87))
        assert tok.is_expired(issued + ABSOLUTE_MAX_AGE)

    def test_seconds_until_expiry(self) -> None:
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        tok = make_token(issued_at=issued)
        assert tok.seconds_until_expiry(issued) == pytest.approx(90 * 60)
        assert tok.seconds_until_expiry(issued + timedelta(minutes=30)) == pytest.approx(60 * 60)


# ---------- parse_access_token_response ------------------------------------


class TestParseAccessTokenResponse:
    good_body = {
        "accessToken": "T",
        "expirationTime": "2026-04-14T13:30:00.000Z",
        "passwordExpirationTime": "2027-01-01T00:00:00.000Z",
        "userStatus": "Active",
        "userId": 42,
        "name": "ed",
        "hasLive": False,
    }

    def test_happy(self) -> None:
        received = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        tok = parse_access_token_response(self.good_body, received)
        assert tok.access_token == "T"
        assert tok.issued_at == received
        assert tok.expires_at == datetime(2026, 4, 14, 13, 30, tzinfo=UTC)
        assert tok.user_id == 42
        assert tok.user_name == "ed"

    def test_rejects_inactive_user(self) -> None:
        body = {**self.good_body, "userStatus": "TemporaryLocked"}
        with pytest.raises(InvalidCredentialsError):
            parse_access_token_response(body, datetime.now(UTC))

    def test_classifies_session_limit_error(self) -> None:
        body = {"errorText": "Maximum concurrent sessions reached"}
        with pytest.raises(SessionLimitError):
            parse_access_token_response(body, datetime.now(UTC))

    def test_classifies_invalid_password_error(self) -> None:
        body = {"errorText": "Invalid password"}
        with pytest.raises(InvalidCredentialsError):
            parse_access_token_response(body, datetime.now(UTC))

    def test_unknown_error_falls_back_to_auth_error(self) -> None:
        body = {"errorText": "Totally novel server gripe"}
        with pytest.raises(AuthError) as ei:
            parse_access_token_response(body, datetime.now(UTC))
        # Must NOT be a subclass — ambiguous errors are the base type
        assert type(ei.value) is AuthError

    def test_missing_field_raises_auth_error(self) -> None:
        body = {k: v for k, v in self.good_body.items() if k != "userId"}
        with pytest.raises(AuthError):
            parse_access_token_response(body, datetime.now(UTC))

    def test_requires_tz_aware_received_at(self) -> None:
        with pytest.raises(ValueError):
            parse_access_token_response(self.good_body, datetime(2026, 4, 14, 12, 0))


class TestParseIsoUtc:
    def test_zulu_suffix(self) -> None:
        dt = _parse_iso_utc("2026-04-14T13:30:00.000Z")
        assert dt == datetime(2026, 4, 14, 13, 30, tzinfo=UTC)

    def test_offset_suffix(self) -> None:
        dt = _parse_iso_utc("2026-04-14T09:30:00-04:00")
        assert dt == datetime(2026, 4, 14, 13, 30, tzinfo=UTC)

    def test_naive_defaults_to_utc(self) -> None:
        dt = _parse_iso_utc("2026-04-14T13:30:00")
        assert dt.tzinfo is UTC


# ---------- TradovateAuthClient --------------------------------------------


def _creds() -> TradovateCreds:
    return TradovateCreds(
        name="u",
        password="p",
        app_id="a",
        app_version="1",
        device_id="d",
        cid="c",
        sec="s",
    )


def _ok_body(access: str = "T", expires: str = "2026-04-14T13:30:00.000Z") -> dict:
    return {
        "accessToken": access,
        "expirationTime": expires,
        "passwordExpirationTime": "2027-01-01T00:00:00.000Z",
        "userStatus": "Active",
        "userId": 42,
        "name": "u",
        "hasLive": False,
    }


class TestTradovateAuthClient:
    async def test_login_sends_creds_and_parses_token(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            import json as _json

            captured["body"] = _json.loads(request.content)
            return httpx.Response(200, json=_ok_body())

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            auth = TradovateAuthClient(hosts_for("demo"), _creds(), http)
            tok = await auth.login()

        assert captured["method"] == "POST"
        assert captured["url"].endswith("/auth/accesstokenrequest")
        assert captured["body"]["name"] == "u"
        assert captured["body"]["cid"] == "c"
        assert tok.access_token == "T"
        assert tok.user_id == 42

    async def test_login_http_error_becomes_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"errorText": "Invalid credentials"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            auth = TradovateAuthClient(hosts_for("demo"), _creds(), http)
            with pytest.raises(InvalidCredentialsError):
                await auth.login()

    async def test_login_session_limit_classified(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errorText": "Maximum concurrent sessions reached"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            auth = TradovateAuthClient(hosts_for("demo"), _creds(), http)
            with pytest.raises(SessionLimitError):
                await auth.login()

    async def test_renew_uses_bearer_header_and_GET(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["auth_header"] = request.headers.get("authorization")
            return httpx.Response(200, json=_ok_body(access="T2"))

        transport = httpx.MockTransport(handler)
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        tok = make_token(issued_at=issued, access="T1")
        async with httpx.AsyncClient(transport=transport) as http:
            auth = TradovateAuthClient(hosts_for("demo"), _creds(), http)
            fresh = await auth.renew(tok)

        assert captured["method"] == "GET"
        assert captured["url"].endswith("/auth/renewAccessToken")
        assert captured["auth_header"] == "Bearer T1"
        assert fresh.access_token == "T2"

    async def test_renew_http_error_is_renewal_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"errorText": "transient"})

        transport = httpx.MockTransport(handler)
        issued = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
        tok = make_token(issued_at=issued)
        async with httpx.AsyncClient(transport=transport) as http:
            auth = TradovateAuthClient(hosts_for("demo"), _creds(), http)
            with pytest.raises(TokenRenewalError):
                await auth.renew(tok)
