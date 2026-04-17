"""Level-1 tests for mnq.venues.tradovate.config."""
from __future__ import annotations

import pytest

from mnq.venues.tradovate.config import Environment, hosts_for


class TestEnvironment:
    @pytest.mark.parametrize("raw,expected", [
        ("demo", Environment.DEMO),
        ("Demo", Environment.DEMO),
        ("paper", Environment.DEMO),
        ("live", Environment.LIVE),
        ("PROD", Environment.LIVE),
        ("production", Environment.LIVE),
    ])
    def test_from_str_accepts_synonyms(self, raw, expected) -> None:
        assert Environment.from_str(raw) is expected

    def test_from_str_rejects_junk(self) -> None:
        with pytest.raises(ValueError):
            Environment.from_str("simulator")


class TestHostsFor:
    def test_demo_hosts(self) -> None:
        h = hosts_for("demo")
        assert h.rest_base == "https://demo.tradovateapi.com/v1"
        assert h.trading_ws.startswith("wss://demo.tradovateapi.com")
        assert "md-demo" in h.market_data_ws

    def test_live_hosts(self) -> None:
        h = hosts_for(Environment.LIVE)
        assert h.rest_base == "https://live.tradovateapi.com/v1"
        assert h.trading_ws.startswith("wss://live.tradovateapi.com")
        assert h.market_data_ws == "wss://md.tradovateapi.com/v1/websocket"
