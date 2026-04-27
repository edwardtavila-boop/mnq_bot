"""Level-1 unit tests for mnq.venues.tradovate.rest.

Covers:
    - BracketParams validation
    - BracketParams.to_params_json / to_request_body shape
    - AccountInfo.from_api
    - TradovateRestClient over httpx.MockTransport:
        - list_accounts parses rows
        - place_order body shape
        - start_bracket sends orderStrategyTypeId=2 and stringified params
        - error classification (non-200, errorText in 200)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from mnq.core.types import Side
from mnq.venues.tradovate.auth import Token
from mnq.venues.tradovate.config import hosts_for
from mnq.venues.tradovate.rest import (
    AccountInfo,
    BracketParams,
    OrderRejectedError,
    TradovateRestClient,
)

UTC = UTC


def _tok() -> Token:
    return Token(
        access_token="TOK",
        expires_at=datetime(2026, 4, 14, 13, 30, tzinfo=UTC),
        issued_at=datetime(2026, 4, 14, 12, 0, tzinfo=UTC),
        user_id=42,
        user_name="u",
        has_live=False,
        user_status="Active",
    )


# ---------- BracketParams ---------------------------------------------------


class TestBracketParams:
    def _base(self, **overrides) -> dict:
        defaults = {
            "account_id": 1,
            "account_spec": "DEMO1",
            "symbol": "MNQM6",
            "side": Side.LONG,
            "qty": 1,
            "profit_target_ticks": 8,
            "stop_loss_ticks": -12,
        }
        defaults.update(overrides)
        return defaults

    def test_happy_market(self) -> None:
        bp = BracketParams(**self._base())
        body = bp.to_request_body()
        assert body["action"] == "Buy"
        assert body["orderStrategyTypeId"] == 2
        params = json.loads(body["params"])
        assert params["entryVersion"] == {"orderType": "Market", "orderQty": 1}
        (bracket,) = params["brackets"]
        assert bracket == {"qty": 1, "profitTarget": 8, "stopLoss": -12, "trailingStop": False}

    def test_short_side_maps_to_sell(self) -> None:
        bp = BracketParams(**self._base(side=Side.SHORT))
        assert bp.to_request_body()["action"] == "Sell"

    def test_limit_entry_includes_price(self) -> None:
        bp = BracketParams(
            **self._base(
                entry_order_type="Limit",
                entry_limit_price=Decimal("18234.25"),
            )
        )
        params = json.loads(bp.to_request_body()["params"])
        assert params["entryVersion"]["orderType"] == "Limit"
        assert params["entryVersion"]["price"] == pytest.approx(18234.25)

    def test_limit_entry_without_price_rejects(self) -> None:
        with pytest.raises(ValueError):
            BracketParams(**self._base(entry_order_type="Limit"))

    def test_rejects_zero_qty(self) -> None:
        with pytest.raises(ValueError):
            BracketParams(**self._base(qty=0))

    def test_rejects_non_positive_profit_target(self) -> None:
        with pytest.raises(ValueError):
            BracketParams(**self._base(profit_target_ticks=0))

    def test_rejects_non_negative_stop_loss(self) -> None:
        with pytest.raises(ValueError):
            BracketParams(**self._base(stop_loss_ticks=0))
        with pytest.raises(ValueError):
            BracketParams(**self._base(stop_loss_ticks=5))

    def test_params_is_stringified(self) -> None:
        """Verifies the crucial encoding — params must be a JSON string,
        not a nested object. Parsers that get this wrong see 'invalid params'."""
        bp = BracketParams(**self._base())
        body = bp.to_request_body()
        assert isinstance(body["params"], str)


# ---------- AccountInfo -----------------------------------------------------


class TestAccountInfo:
    def test_from_api_happy(self) -> None:
        row = {
            "id": 123456,
            "name": "DEMO123456",
            "accountType": "Customer",
            "active": True,
            "archived": False,
        }
        ai = AccountInfo.from_api(row)
        assert ai.id == 123456
        assert ai.name == "DEMO123456"
        assert ai.active is True

    def test_defaults_when_fields_missing(self) -> None:
        row = {"id": 1, "name": "x"}
        ai = AccountInfo.from_api(row)
        assert ai.account_type == ""
        assert ai.active is True  # default — Tradovate omits this on active accts
        assert ai.archived is False


# ---------- TradovateRestClient --------------------------------------------


class TestRestClient:
    async def test_list_accounts_parses_rows(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path.endswith("/account/list")
            assert request.headers["authorization"] == "Bearer TOK"
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "A",
                        "accountType": "Customer",
                        "active": True,
                        "archived": False,
                    },
                    {
                        "id": 2,
                        "name": "B",
                        "accountType": "Customer",
                        "active": True,
                        "archived": False,
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rc = TradovateRestClient(hosts_for("demo"), _tok, http)
            accts = await rc.list_accounts()

        assert [a.name for a in accts] == ["A", "B"]

    async def test_place_order_body_shape(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["path"] = request.url.path
            return httpx.Response(200, json={"orderId": 9999})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rc = TradovateRestClient(hosts_for("demo"), _tok, http)
            out = await rc.place_order(
                account_id=1,
                account_spec="DEMO1",
                symbol="MNQM6",
                side=Side.SHORT,
                qty=1,
            )

        assert captured["path"].endswith("/order/placeorder")
        assert captured["body"]["action"] == "Sell"
        assert captured["body"]["orderQty"] == 1
        assert captured["body"]["orderType"] == "Market"
        assert captured["body"]["symbol"] == "MNQM6"
        assert captured["body"]["isAutomated"] is True
        assert out == {"orderId": 9999}

    async def test_start_bracket_sends_stringified_params(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["path"] = request.url.path
            return httpx.Response(200, json={"orderStrategy": {"id": 777}})

        bp = BracketParams(
            account_id=1,
            account_spec="DEMO1",
            symbol="MNQM6",
            side=Side.LONG,
            qty=1,
            profit_target_ticks=8,
            stop_loss_ticks=-12,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rc = TradovateRestClient(hosts_for("demo"), _tok, http)
            await rc.start_bracket(bp)

        assert captured["path"].endswith("/orderStrategy/startorderstrategy")
        assert captured["body"]["orderStrategyTypeId"] == 2
        assert isinstance(captured["body"]["params"], str)
        params = json.loads(captured["body"]["params"])
        assert params["brackets"][0]["profitTarget"] == 8

    async def test_400_raises_order_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"errorText": "bad symbol"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rc = TradovateRestClient(hosts_for("demo"), _tok, http)
            with pytest.raises(OrderRejectedError) as ei:
                await rc.list_accounts()
            assert ei.value.status == 400
            assert "bad symbol" in str(ei.value)

    async def test_200_with_errorText_still_raises(self) -> None:
        """Tradovate embeds errors in 200-OK bodies for some order paths."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errorText": "insufficient buying power"})

        bp = BracketParams(
            account_id=1,
            account_spec="DEMO1",
            symbol="MNQM6",
            side=Side.LONG,
            qty=1,
            profit_target_ticks=8,
            stop_loss_ticks=-12,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rc = TradovateRestClient(hosts_for("demo"), _tok, http)
            with pytest.raises(OrderRejectedError) as ei:
                await rc.start_bracket(bp)
            assert "insufficient" in str(ei.value)

    async def test_200_with_failureReason_still_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "failureReason": "RiskCheckFailed",
                    "failureText": "margin",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            rc = TradovateRestClient(hosts_for("demo"), _tok, http)
            with pytest.raises(OrderRejectedError):
                await rc.place_order(
                    account_id=1,
                    account_spec="DEMO1",
                    symbol="MNQM6",
                    side=Side.LONG,
                    qty=1,
                )
