"""[REAL] Tradovate REST client — accounts, contracts, orders, OCO brackets.

See `docs/TRADOVATE_NOTES.md` §4-5.

Scope of this module:
    - Look up accounts (`/account/list`) — needed once per session.
    - Resolve the front-month MNQ contract (`/contract/find`).
    - Place plain orders (`/order/placeorder`) — used for emergency flatten
      only; normal entries go through the bracket strategy below.
    - Place OCO brackets (`/orderStrategy/startorderstrategy`,
      `orderStrategyTypeId=2`) — **the** entry path.
    - Cancel orders and order strategies.

All methods are async. The client is stateless except for the injected
httpx transport and a callable that returns the current Token. That
callable decouples the REST client from the renewal scheduler.

The OCO bracket encoding is fiddly — see `BracketParams.to_params_json`.
`params` is a JSON *string*, not a nested JSON object, per the forum
example and confirmed in practice by Tradovate staff.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import httpx

from mnq.core.types import Side
from mnq.venues.tradovate.auth import Token
from mnq.venues.tradovate.config import Hosts

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OrderRejectedError(Exception):
    """Tradovate returned non-2xx or an errorText on an order submission."""

    def __init__(
        self, message: str, *, body: dict[str, Any] | None = None, status: int | None = None
    ):
        super().__init__(message)
        self.body = body
        self.status = status


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """One row from `/account/list`, filtered to the fields we use."""

    id: int
    name: str
    account_type: str  # 'Customer', 'Hedger', etc.
    active: bool
    archived: bool

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> AccountInfo:
        return cls(
            id=int(row["id"]),
            name=row["name"],
            account_type=row.get("accountType", ""),
            active=bool(row.get("active", True)),
            archived=bool(row.get("archived", False)),
        )


@dataclass(frozen=True, slots=True)
class BracketParams:
    """Inputs for `/orderStrategy/startorderstrategy` with Brackets type.

    `profit_target_ticks` and `stop_loss_ticks` are signed integers
    relative to entry; by Tradovate convention profit is positive
    (favorable for the entry direction) and stop is negative.
    """

    account_id: int
    account_spec: str
    symbol: str
    side: Side
    qty: int
    profit_target_ticks: int
    stop_loss_ticks: int
    entry_order_type: Literal["Market", "Limit"] = "Market"
    entry_limit_price: Decimal | None = None
    trailing_stop: bool = False

    def __post_init__(self) -> None:
        if self.qty < 1:
            raise ValueError(f"qty must be >= 1, got {self.qty}")
        if self.profit_target_ticks <= 0:
            raise ValueError(f"profit_target_ticks must be > 0, got {self.profit_target_ticks}")
        if self.stop_loss_ticks >= 0:
            raise ValueError(f"stop_loss_ticks must be < 0, got {self.stop_loss_ticks}")
        if self.entry_order_type == "Limit" and self.entry_limit_price is None:
            raise ValueError("entry_limit_price required when entry_order_type=Limit")

    def action(self) -> str:
        return "Buy" if self.side is Side.LONG else "Sell"

    def to_params_json(self) -> str:
        """Produce the stringified `params` field per forum/docs."""
        entry: dict[str, Any] = {
            "orderType": self.entry_order_type,
            "orderQty": self.qty,
        }
        if self.entry_order_type == "Limit":
            # Decimal → float at JSON boundary; callers are responsible for
            # passing tick-aligned prices.
            entry["price"] = float(self.entry_limit_price)  # type: ignore[arg-type]

        bracket = {
            "qty": self.qty,
            "profitTarget": self.profit_target_ticks,
            "stopLoss": self.stop_loss_ticks,
            "trailingStop": self.trailing_stop,
        }
        return json.dumps({"entryVersion": entry, "brackets": [bracket]})

    def to_request_body(self) -> dict[str, Any]:
        return {
            "accountId": self.account_id,
            "accountSpec": self.account_spec,
            "symbol": self.symbol,
            "action": self.action(),
            "orderStrategyTypeId": 2,  # Brackets — see TRADOVATE_NOTES §4
            "params": self.to_params_json(),
        }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

TokenProvider = Callable[[], Token]


class TradovateRestClient:
    """Thin typed wrapper over the Tradovate REST surface we use.

    The `token_provider` is a zero-arg callable returning the currently
    valid Token. A background renewer (not implemented in Step 1) will
    atomically swap what this callable returns; REST methods pick up the
    new token on their next call automatically.
    """

    def __init__(
        self,
        hosts: Hosts,
        token_provider: TokenProvider,
        http: httpx.AsyncClient,
    ):
        self._hosts = hosts
        self._token_provider = token_provider
        self._http = http

    # ---- internal helpers --------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        tok = self._token_provider()
        return {"Authorization": f"Bearer {tok.access_token}"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._hosts.rest_base}{path}"
        resp = await self._http.get(url, params=params, headers=self._auth_headers())
        return self._handle(resp)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self._hosts.rest_base}{path}"
        resp = await self._http.post(url, json=body, headers=self._auth_headers())
        return self._handle(resp)

    @staticmethod
    def _handle(resp: httpx.Response) -> Any:
        try:
            body = resp.json()
        except ValueError as e:
            raise OrderRejectedError(
                f"non-JSON response (status={resp.status_code}): {resp.text[:200]!r}",
                status=resp.status_code,
            ) from e
        if resp.status_code >= 400:
            err = (body or {}).get("errorText") or f"HTTP {resp.status_code}"
            raise OrderRejectedError(err, body=body, status=resp.status_code)
        # Tradovate embeds errors in 200-OK bodies for orders.
        if isinstance(body, dict) and body.get("errorText"):
            raise OrderRejectedError(body["errorText"], body=body, status=resp.status_code)
        if isinstance(body, dict) and body.get("failureReason"):
            raise OrderRejectedError(
                body.get("failureText") or body["failureReason"],
                body=body,
                status=resp.status_code,
            )
        return body

    # ---- accounts ----------------------------------------------------------

    async def list_accounts(self) -> list[AccountInfo]:
        """`GET /account/list` — accounts visible to the authed user."""
        rows = await self._get("/account/list")
        return [AccountInfo.from_api(r) for r in rows]

    # ---- contracts ---------------------------------------------------------

    async def find_contract(self, name: str) -> dict[str, Any]:
        """`GET /contract/find?name=<symbol>` — resolve a single contract."""
        result: dict[str, Any] = await self._get("/contract/find", params={"name": name})
        return result

    async def list_contracts_matching(self, root: str) -> list[dict[str, Any]]:
        """`GET /contract/suggest?t=<root>&l=10` — list front-month candidates."""
        result: list[dict[str, Any]] = await self._get(
            "/contract/suggest", params={"t": root, "l": 10}
        )
        return result

    # ---- orders ------------------------------------------------------------

    async def place_order(
        self,
        *,
        account_id: int,
        account_spec: str,
        symbol: str,
        side: Side,
        qty: int,
        order_type: str = "Market",
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        time_in_force: str = "Day",
        is_automated: bool = True,
    ) -> dict[str, Any]:
        """`POST /order/placeorder`. Used for emergency flatten; normal entries
        go through `start_bracket`."""
        body: dict[str, Any] = {
            "accountId": account_id,
            "accountSpec": account_spec,
            "action": "Buy" if side is Side.LONG else "Sell",
            "symbol": symbol,
            "orderQty": qty,
            "orderType": order_type,
            "timeInForce": time_in_force,
            "isAutomated": is_automated,
        }
        if price is not None:
            body["price"] = float(price)
        if stop_price is not None:
            body["stopPrice"] = float(stop_price)
        result: dict[str, Any] = await self._post("/order/placeorder", body)
        return result

    async def cancel_order(self, order_id: int) -> dict[str, Any]:
        result: dict[str, Any] = await self._post("/order/cancelorder", {"orderId": order_id})
        return result

    # ---- order strategies (brackets) ---------------------------------------

    async def start_bracket(self, params: BracketParams) -> dict[str, Any]:
        """`POST /orderStrategy/startorderstrategy` — atomic entry + OCO bracket.

        Per Hard Rule "no unprotected position, ever", callers that receive
        an error from this method after any fill has occurred MUST immediately
        market-close via `place_order`. The executor, not this client,
        enforces that invariant.
        """
        result: dict[str, Any] = await self._post(
            "/orderStrategy/startorderstrategy", params.to_request_body()
        )
        return result

    async def cancel_order_strategy(self, strategy_id: int) -> dict[str, Any]:
        result: dict[str, Any] = await self._post(
            "/orderStrategy/cancel", {"orderStrategyId": strategy_id}
        )
        return result

    # ---- misc --------------------------------------------------------------

    async def auth_me(self) -> dict[str, Any]:
        """`GET /auth/me` — cheap auth sanity probe."""
        result: dict[str, Any] = await self._get("/auth/me")
        return result
