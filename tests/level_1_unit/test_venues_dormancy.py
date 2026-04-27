"""Tests for ``mnq.venues.dormancy`` (B5 closure -- Red Team review 2026-04-25).

The dormancy set is the single source of truth for "which brokers are
operationally off-limits." Tests pin:

* DORMANT_BROKERS contains tradovate (operator mandate 2026-04-24)
* assert_broker_active raises DormantBrokerError on a dormant name
* assert_broker_active is silent on an active name
* is_broker_dormant predicate matches assert_broker_active behaviour
* case-insensitive matching
* whitespace-tolerant matching
"""

from __future__ import annotations

import pytest

from mnq.venues.dormancy import (
    DORMANT_BROKERS,
    DormantBrokerError,
    assert_broker_active,
    is_broker_dormant,
)


class TestDormantBrokersSet:
    def test_tradovate_is_dormant(self) -> None:
        # Per CLAUDE.md operator mandate (2026-04-24)
        assert "tradovate" in DORMANT_BROKERS

    def test_dormant_set_is_immutable(self) -> None:
        # frozenset prevents accidental mutation at runtime.
        assert isinstance(DORMANT_BROKERS, frozenset)

    def test_active_brokers_not_in_dormant_set(self) -> None:
        for active in ("ibkr", "tastytrade"):
            assert active not in DORMANT_BROKERS, (
                f"{active!r} is supposed to be active per the broker "
                f"dormancy mandate but is in DORMANT_BROKERS"
            )


class TestAssertBrokerActive:
    def test_active_broker_passes(self) -> None:
        # Should not raise.
        assert_broker_active("ibkr")
        assert_broker_active("tastytrade")

    def test_dormant_broker_raises(self) -> None:
        with pytest.raises(DormantBrokerError, match="tradovate"):
            assert_broker_active("tradovate")

    def test_dormant_error_message_names_override_path(self) -> None:
        """Operator must be able to grep the error to find the
        dormancy override location."""
        try:
            assert_broker_active("tradovate")
        except DormantBrokerError as exc:
            msg = str(exc)
            assert "DORMANT_BROKERS" in msg
            assert "src/mnq/venues/dormancy.py" in msg

    def test_case_insensitive(self) -> None:
        with pytest.raises(DormantBrokerError):
            assert_broker_active("TRADOVATE")
        with pytest.raises(DormantBrokerError):
            assert_broker_active("Tradovate")

    def test_whitespace_tolerant(self) -> None:
        with pytest.raises(DormantBrokerError):
            assert_broker_active("  tradovate  ")


class TestIsBrokerDormant:
    def test_returns_true_for_dormant(self) -> None:
        assert is_broker_dormant("tradovate") is True

    def test_returns_false_for_active(self) -> None:
        assert is_broker_dormant("ibkr") is False
        assert is_broker_dormant("tastytrade") is False

    def test_returns_false_for_unknown(self) -> None:
        # Unknown brokers are NOT in the dormant set; the dormancy
        # check is a denylist not an allowlist. (An allowlist check
        # is a separate doctor responsibility.)
        assert is_broker_dormant("totally_made_up_broker") is False

    def test_case_insensitive(self) -> None:
        assert is_broker_dormant("TRADOVATE") is True
        assert is_broker_dormant("TradoVate") is True


class TestDormancyDoctorIntegration:
    """Verify the doctor check picks up env-configured brokers."""

    def test_doctor_check_passes_with_no_broker_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Strip any inherited env vars
        for var in ("BROKER_TYPE", "APEX_BROKER", "MNQ_LIVE_BROKER"):
            monkeypatch.delenv(var, raising=False)
        from mnq.cli.doctor import _check_broker_dormancy

        result = _check_broker_dormancy()
        assert result.status == "ok"
        assert "no live broker" in result.detail

    def test_doctor_check_fails_with_dormant_broker(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for var in ("APEX_BROKER", "MNQ_LIVE_BROKER"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("BROKER_TYPE", "tradovate")
        from mnq.cli.doctor import _check_broker_dormancy

        result = _check_broker_dormancy()
        assert result.status == "fail"
        assert "tradovate" in result.detail.lower()

    def test_doctor_check_passes_with_active_broker(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for var in ("APEX_BROKER", "MNQ_LIVE_BROKER"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("BROKER_TYPE", "ibkr")
        from mnq.cli.doctor import _check_broker_dormancy

        result = _check_broker_dormancy()
        assert result.status == "ok"
        assert "ibkr" in result.detail
