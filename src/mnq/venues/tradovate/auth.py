"""[REAL] Tradovate authentication — login, token lifetime tracking, renewal.

See `docs/TRADOVATE_NOTES.md` §2.

Critical design constraints encoded here:

1. `login()` calls `POST /auth/accesstokenrequest`. This creates a new
   *session* server-side. Users are capped at 2 concurrent sessions, so
   we never call this path to refresh — renewal goes through
   `/auth/renewAccessToken` only.
2. A `Token` tracks both `expires_at` (the server-provided deadline) and
   `issued_at` (the client-observed acquisition time). Renewal is driven
   by `issued_at` age — we renew at ~75 min elapsed, not by watching the
   absolute `expires_at`, because server clock skew is real and issuing
   fresh requests 15 min before the clocks agree avoids a cliff.
3. Tokens are immutable `@frozen` dataclasses. Renewal returns a new
   Token; the executor/caller is responsible for atomically swapping it.
4. The HTTP layer is dependency-injected: callers pass in an
   `httpx.AsyncClient`. Tests use `httpx.MockTransport`. No module-level
   globals.

Exception taxonomy:

    AuthError (base)
      ├─ InvalidCredentialsError   — wrong name/password/sec
      ├─ SessionLimitError         — hit the 2-session cap somehow
      └─ TokenRenewalError         — /renewAccessToken failed (token
                                     maybe still valid, caller decides)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from mnq.venues.tradovate.config import Environment, hosts_for


def _stable_hash(s: str) -> int:
    """SHA-256-based stable hash. Unlike `hash()` this is deterministic
    across Python processes (no PYTHONHASHSEED sensitivity)."""
    return int.from_bytes(hashlib.sha256(s.encode("utf-8")).digest()[:8], "big")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base class for all Tradovate auth failures."""


class InvalidCredentialsError(AuthError):
    """Credentials rejected by Tradovate (userStatus != Active, wrong pw, etc)."""


class SessionLimitError(AuthError):
    """Tradovate reports the 2-concurrent-session cap was hit."""


class TokenRenewalError(AuthError):
    """`/auth/renewAccessToken` failed. Caller decides whether to re-login."""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradovateCreds:
    """Credentials required to call /auth/accesstokenrequest.

    `app_id` / `app_version` / `device_id` are sent to Tradovate but are
    informational on their side; `cid` and `sec` are the app secret pair
    issued to you when you register for API access.
    """

    name: str
    password: str
    app_id: str
    app_version: str
    device_id: str
    cid: str
    sec: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> TradovateCreds:
        """Build from process env (or a supplied dict) matching `.env.example`."""
        e = env if env is not None else os.environ
        missing = [
            k
            for k in (
                "TV_USERNAME",
                "TV_PASSWORD",
                "TV_APP_ID",
                "TV_APP_VERSION",
                "TV_DEVICE_ID",
                "TV_CID",
                "TV_SEC",
            )
            if not e.get(k)
        ]
        if missing:
            raise ValueError(
                f"missing required Tradovate env vars: {', '.join(missing)}; see .env.example"
            )
        return cls(
            name=e["TV_USERNAME"],
            password=e["TV_PASSWORD"],
            app_id=e["TV_APP_ID"],
            app_version=e["TV_APP_VERSION"],
            device_id=e["TV_DEVICE_ID"],
            cid=e["TV_CID"],
            sec=e["TV_SEC"],
        )

    def as_request_body(self) -> dict[str, str]:
        """JSON body for /auth/accesstokenrequest."""
        return {
            "name": self.name,
            "password": self.password,
            "appId": self.app_id,
            "appVersion": self.app_version,
            "deviceId": self.device_id,
            "cid": self.cid,
            "sec": self.sec,
        }


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

# Default renewal threshold: 75 minutes elapsed since issue. Handoff spec.
DEFAULT_RENEW_AT = timedelta(minutes=75)

# Hard ceiling beyond which we treat the token as effectively dead regardless
# of the server's expiration time. Acts as a safety net if clocks skew badly.
ABSOLUTE_MAX_AGE = timedelta(minutes=88)

# Jitter bound applied to `needs_renewal` when a process-local jitter key is
# provided. Multiple instances of the bot share the same credentials and will
# all cross the threshold within the same second, slamming
# `/auth/renewAccessToken` in lockstep. The jitter spreads those calls across
# a 2-minute window (threshold ± 60s) based on a stable hash of the jitter
# key (e.g. host+pid+token_issued_at), so each instance picks a deterministic
# offset that different instances almost certainly do not share.
DEFAULT_RENEW_JITTER = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class Token:
    """An access token with observed issue time and server-declared expiry."""

    access_token: str
    expires_at: datetime  # server-declared, UTC
    issued_at: datetime  # client-observed acquisition time, UTC
    user_id: int
    user_name: str
    has_live: bool
    user_status: str

    # --- lifetime queries (pure functions of now) ---------------------------

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or datetime.now(UTC)) - self.issued_at

    def seconds_until_expiry(self, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        return (self.expires_at - now).total_seconds()

    def needs_renewal(
        self,
        now: datetime | None = None,
        threshold: timedelta = DEFAULT_RENEW_AT,
        *,
        jitter_key: str | None = None,
        jitter: timedelta = DEFAULT_RENEW_JITTER,
    ) -> bool:
        """True iff this token should be renewed at `now`.

        When `jitter_key` is provided, the effective threshold is
        `threshold + offset` where `offset ∈ [-jitter, +jitter]` is
        deterministic in the key. This spreads renewal attempts across
        fleet instances so they don't all hit `/auth/renewAccessToken`
        at the same millisecond (Tradovate rate-limits aggressively on
        auth endpoints).
        """
        effective = threshold
        if jitter_key is not None and jitter.total_seconds() > 0:
            # Stable [-1.0, +1.0] offset from a 63-bit hash.
            h = _stable_hash(jitter_key)
            frac = ((h & ((1 << 63) - 1)) / float(1 << 62)) - 1.0
            effective = threshold + timedelta(seconds=frac * jitter.total_seconds())
        return self.age(now) >= effective

    def is_expired(self, now: datetime | None = None) -> bool:
        """Conservative: expired if either server clock says so OR we're past
        ABSOLUTE_MAX_AGE since issue."""
        now = now or datetime.now(UTC)
        return now >= self.expires_at or self.age(now) >= ABSOLUTE_MAX_AGE


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_access_token_response(body: dict[str, Any], received_at: datetime) -> Token:
    """Build a Token from an AccessTokenResponse body.

    Raises:
        InvalidCredentialsError: if the response has `errorText` populated or
            `userStatus` is anything other than 'Active'.
        AuthError: if required fields are missing.
    """
    err = body.get("errorText")
    if err:
        _classify_error_text(err)

    try:
        access_token = body["accessToken"]
        expires_at_raw = body["expirationTime"]
        user_status = body["userStatus"]
        user_id = int(body["userId"])
        user_name = body["name"]
        has_live = bool(body.get("hasLive", False))
    except KeyError as e:
        raise AuthError(f"AccessTokenResponse missing field: {e!s}") from e

    if user_status != "Active":
        raise InvalidCredentialsError(
            f"userStatus={user_status!r} (want 'Active'); check your Tradovate account"
        )

    expires_at = _parse_iso_utc(expires_at_raw)
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware (UTC)")

    return Token(
        access_token=access_token,
        expires_at=expires_at,
        issued_at=received_at,
        user_id=user_id,
        user_name=user_name,
        has_live=has_live,
        user_status=user_status,
    )


def _classify_error_text(err: str) -> None:
    """Map Tradovate's free-form error strings to our exception taxonomy."""
    low = err.lower()
    if "session" in low and ("limit" in low or "concurrent" in low or "maximum" in low):
        raise SessionLimitError(err)
    if any(k in low for k in ("password", "credential", "invalid", "captcha", "locked")):
        raise InvalidCredentialsError(err)
    raise AuthError(err)


def _parse_iso_utc(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp (Tradovate uses e.g. '2026-04-14T20:30:00.000Z')."""
    # Python's fromisoformat doesn't accept 'Z' until 3.11+ — we target 3.12.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class TradovateAuthClient:
    """Async auth client. Owns no state beyond HTTP; Token ownership is external.

    Typical usage::

        async with httpx.AsyncClient(timeout=15) as http:
            auth = TradovateAuthClient(hosts_for("demo"), creds, http)
            tok = await auth.login()
            # ... later, when tok.needs_renewal() ...
            tok = await auth.renew(tok)
    """

    def __init__(
        self,
        hosts: Any,  # Hosts, but imported lazily for cleaner test imports
        creds: TradovateCreds,
        http: httpx.AsyncClient,
        *,
        clock: Any = None,
    ):
        self._hosts = hosts
        self._creds = creds
        self._http = http
        self._clock = clock or (lambda: datetime.now(UTC))

    # Convenient constructor that wires up hosts from env string.
    @classmethod
    def for_env(
        cls,
        env: Environment | str,
        creds: TradovateCreds,
        http: httpx.AsyncClient,
    ) -> TradovateAuthClient:
        return cls(hosts_for(env), creds, http)

    async def login(self) -> Token:
        """POST /auth/accesstokenrequest — starts a new session. Call sparingly."""
        url = f"{self._hosts.rest_base}/auth/accesstokenrequest"
        resp = await self._http.post(url, json=self._creds.as_request_body())
        received_at = self._clock()
        return self._parse(resp, received_at)

    async def renew(self, current: Token) -> Token:
        """GET /auth/renewAccessToken — extends the existing session.

        Pass the currently-valid token; we send it as the bearer header.
        Tradovate returns a fresh AccessTokenResponse with a new expiry.
        """
        url = f"{self._hosts.rest_base}/auth/renewAccessToken"
        headers = {"Authorization": f"Bearer {current.access_token}"}
        try:
            resp = await self._http.get(url, headers=headers)
            received_at = self._clock()
            return self._parse(resp, received_at, renewal=True)
        except httpx.HTTPError as e:
            raise TokenRenewalError(f"network error during renew: {e}") from e

    # ---- internal helpers --------------------------------------------------

    def _parse(
        self,
        resp: httpx.Response,
        received_at: datetime,
        *,
        renewal: bool = False,
    ) -> Token:
        try:
            body = resp.json()
        except ValueError as e:
            raise AuthError(
                f"non-JSON response from auth endpoint (status={resp.status_code})"
            ) from e

        if resp.status_code >= 400:
            err = (body or {}).get("errorText") or f"HTTP {resp.status_code}"
            if renewal:
                raise TokenRenewalError(err)
            _classify_error_text(err)
            raise AuthError(err)  # unreachable; _classify always raises

        return parse_access_token_response(body, received_at)
