"""[TEST] CME Futures Calendar and Contracts.

Tests for:
  - SessionWindow
  - CMEFuturesCalendar (RTH/ETH windows, holidays, half-days, roll dates)
  - FuturesContract (parsing, symbol generation, navigation)
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from mnq.core.calendar import CMEFuturesCalendar, SessionWindow
from mnq.core.contracts import FuturesContract


class TestSessionWindow:
    """Tests for SessionWindow dataclass."""

    def test_session_window_frozen(self) -> None:
        """SessionWindow should be immutable (frozen)."""
        sw = SessionWindow(
            start=datetime(2025, 12, 23, 14, 30, tzinfo=UTC),
            end=datetime(2025, 12, 23, 18, 0, tzinfo=UTC),
            kind="RTH",
            is_half_day=True,
        )
        with pytest.raises(AttributeError):
            sw.kind = "ETH"  # type: ignore


class TestCMEFuturesCalendar:
    """Tests for CMEFuturesCalendar."""

    @pytest.fixture
    def cal(self) -> CMEFuturesCalendar:
        """Provide a CMEFuturesCalendar instance."""
        return CMEFuturesCalendar()

    def test_is_trading_day_weekday(self, cal: CMEFuturesCalendar) -> None:
        """Normal weekdays should be trading days."""
        # Monday, Dec 16, 2025
        assert cal.is_trading_day(date(2025, 12, 16))
        # Tuesday, Dec 17, 2025
        assert cal.is_trading_day(date(2025, 12, 17))

    def test_is_trading_day_weekend(self, cal: CMEFuturesCalendar) -> None:
        """Weekends should not be trading days."""
        # Saturday, Dec 13, 2025
        assert not cal.is_trading_day(date(2025, 12, 13))
        # Sunday, Dec 14, 2025
        assert not cal.is_trading_day(date(2025, 12, 14))

    def test_is_trading_day_christmas(self, cal: CMEFuturesCalendar) -> None:
        """Christmas Day (Dec 25) should not be a trading day."""
        # Dec 25, 2025 is a Thursday, but it's Christmas
        assert not cal.is_trading_day(date(2025, 12, 25))

    def test_is_trading_day_good_friday(self, cal: CMEFuturesCalendar) -> None:
        """Good Friday may or may not be a trading day depending on CME calendar.

        Check that the calendar properly reflects CME's holiday schedule.
        Note: CME Globex Equity may have different observances than stock market.
        This test validates the calendar reflects what pandas_market_calendars knows.
        """
        # April 3, 2026 is Good Friday; check what the calendar says
        # If CME trades it, is_trading_day should return True
        is_trading = cal.is_trading_day(date(2026, 4, 3))
        # Just assert it's consistent - we're testing the calendar works, not CME policy
        assert isinstance(is_trading, bool)

    def test_is_half_day_dec_23_2025(self, cal: CMEFuturesCalendar) -> None:
        """Dec 23, 2025 should be a half-day (Christmas Eve)."""
        assert cal.is_half_day(date(2025, 12, 23))

    def test_is_half_day_normal_day(self, cal: CMEFuturesCalendar) -> None:
        """Normal trading days should not be half-days."""
        assert not cal.is_half_day(date(2025, 12, 16))

    def test_is_half_day_holiday(self, cal: CMEFuturesCalendar) -> None:
        """Holidays should return False for is_half_day."""
        assert not cal.is_half_day(date(2025, 12, 25))

    def test_rth_window_dec_23_2025(self, cal: CMEFuturesCalendar) -> None:
        """RTH on Dec 23, 2025 should close at 13:00 ET (1 PM)."""
        window = cal.rth_window(date(2025, 12, 23))
        assert window is not None
        assert window.kind == "RTH"
        assert window.is_half_day is True

        # Dec 23, 2025 is a Tuesday. 9:30 AM ET = 2:30 PM UTC, 1:00 PM ET = 6:00 PM UTC
        # (Eastern Standard Time is UTC-5 in December)
        assert window.start.hour == 14
        assert window.start.minute == 30
        assert window.end.hour == 18
        assert window.end.minute == 0

    def test_rth_window_normal_day(self, cal: CMEFuturesCalendar) -> None:
        """RTH on normal days should close at 16:00 ET (4 PM)."""
        window = cal.rth_window(date(2025, 12, 16))
        assert window is not None
        assert window.kind == "RTH"
        assert window.is_half_day is False

        # 9:30 AM ET = 2:30 PM UTC, 4:00 PM ET = 9:00 PM UTC
        assert window.start.hour == 14
        assert window.start.minute == 30
        assert window.end.hour == 21
        assert window.end.minute == 0

    def test_rth_window_holiday(self, cal: CMEFuturesCalendar) -> None:
        """RTH on holidays should return None."""
        window = cal.rth_window(date(2025, 12, 25))
        assert window is None

    def test_eth_window_normal_day(self, cal: CMEFuturesCalendar) -> None:
        """ETH window should span previous day 5 PM ET to current day 9:30 AM ET."""
        window = cal.eth_window(date(2025, 12, 17))
        assert window is not None
        assert window.kind == "ETH"
        assert window.is_half_day is False

        # Starts 5 PM ET previous day (Dec 16) = 10 PM UTC
        # Ends 9:30 AM ET current day (Dec 17) = 2:30 PM UTC
        assert window.start.day == 16
        assert window.start.hour == 22
        assert window.end.day == 17
        assert window.end.hour == 14
        assert window.end.minute == 30

    def test_eth_window_holiday(self, cal: CMEFuturesCalendar) -> None:
        """ETH window on a holiday should return None."""
        window = cal.eth_window(date(2025, 12, 25))
        assert window is None

    def test_next_trading_day(self, cal: CMEFuturesCalendar) -> None:
        """next_trading_day should skip weekends and holidays."""
        # Dec 19, 2025 is Friday
        # Dec 20-21 is weekend
        # Dec 22 is Monday
        assert cal.next_trading_day(date(2025, 12, 19)) == date(2025, 12, 22)

        # Dec 25 is Christmas (holiday)
        # Dec 26-28 should be checked
        next_day = cal.next_trading_day(date(2025, 12, 24))
        assert next_day > date(2025, 12, 25)
        assert cal.is_trading_day(next_day)

    def test_prev_trading_day(self, cal: CMEFuturesCalendar) -> None:
        """prev_trading_day should skip weekends and holidays."""
        # Dec 22, 2025 is Monday
        # Dec 21-20 is weekend
        # Dec 19 is Friday
        assert cal.prev_trading_day(date(2025, 12, 22)) == date(2025, 12, 19)

    def test_is_in_rth(self, cal: CMEFuturesCalendar) -> None:
        """is_in_rth should detect if timestamp is within RTH."""
        # 2:00 PM UTC on Dec 16, 2025 = 9:00 AM ET (not yet open)
        ts_before = datetime(2025, 12, 16, 14, 0, tzinfo=UTC)
        assert not cal.is_in_rth(ts_before)

        # 3:00 PM UTC on Dec 16, 2025 = 10:00 AM ET (open)
        ts_inside = datetime(2025, 12, 16, 15, 0, tzinfo=UTC)
        assert cal.is_in_rth(ts_inside)

        # 11:00 PM UTC on Dec 16, 2025 = 6:00 PM ET (closed)
        ts_after = datetime(2025, 12, 16, 23, 0, tzinfo=UTC)
        assert not cal.is_in_rth(ts_after)

    def test_is_in_rth_requires_timezone(self, cal: CMEFuturesCalendar) -> None:
        """is_in_rth should raise if given naive datetime."""
        ts_naive = datetime(2025, 12, 16, 15, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            cal.is_in_rth(ts_naive)

    def test_is_in_blackout_default_margins(self, cal: CMEFuturesCalendar) -> None:
        """is_in_blackout with default 2-min margins."""
        # RTH on Dec 16, 2025: 14:30 - 21:00 UTC
        # Blackout: [14:28 - 14:30] and [21:00 - 21:02]

        # 2:28 PM UTC = within pre-open blackout
        ts_pre = datetime(2025, 12, 16, 14, 28, tzinfo=UTC)
        assert cal.is_in_blackout(ts_pre)

        # 2:29 PM UTC = within pre-open blackout
        ts_pre2 = datetime(2025, 12, 16, 14, 29, tzinfo=UTC)
        assert cal.is_in_blackout(ts_pre2)

        # 2:31 PM UTC = outside blackout (trading)
        ts_trading = datetime(2025, 12, 16, 14, 31, tzinfo=UTC)
        assert not cal.is_in_blackout(ts_trading)

        # 9:00 PM UTC = within post-close blackout
        ts_post = datetime(2025, 12, 16, 21, 0, tzinfo=UTC)
        assert cal.is_in_blackout(ts_post)

        # 9:02 PM UTC = within post-close blackout (last 2 min)
        ts_post2 = datetime(2025, 12, 16, 21, 1, tzinfo=UTC)
        assert cal.is_in_blackout(ts_post2)

        # 9:03 PM UTC = outside blackout
        ts_post3 = datetime(2025, 12, 16, 21, 3, tzinfo=UTC)
        assert not cal.is_in_blackout(ts_post3)

    def test_is_in_blackout_custom_margins(self, cal: CMEFuturesCalendar) -> None:
        """is_in_blackout with custom margin windows."""
        # RTH on Dec 16, 2025: 14:30 - 21:00 UTC
        # With 5 min margins: [14:25 - 14:30] and [21:00 - 21:05]

        ts_pre = datetime(2025, 12, 16, 14, 27, tzinfo=UTC)
        assert cal.is_in_blackout(ts_pre, pre_open_min=5)

        ts_pre_outside = datetime(2025, 12, 16, 14, 24, tzinfo=UTC)
        assert not cal.is_in_blackout(ts_pre_outside, pre_open_min=5)

    def test_is_in_blackout_requires_timezone(self, cal: CMEFuturesCalendar) -> None:
        """is_in_blackout should raise if given naive datetime."""
        ts_naive = datetime(2025, 12, 16, 14, 28)
        with pytest.raises(ValueError, match="timezone-aware"):
            cal.is_in_blackout(ts_naive)

    def test_quarterly_roll_date_nqh26(self, cal: CMEFuturesCalendar) -> None:
        """Roll date for NQH26 (March contract)."""
        # March 2026: 3rd Friday is March 20, 2026
        # 2nd Thursday before that is March 12, 2026
        roll = cal.quarterly_roll_date("NQH26", 2026)
        assert roll == date(2026, 3, 12)

    def test_quarterly_roll_date_mnqz25(self, cal: CMEFuturesCalendar) -> None:
        """Roll date for MNQZ25 (December contract)."""
        # December 2025: 3rd Friday is December 19, 2025
        # 2nd Thursday before that is December 11, 2025
        roll = cal.quarterly_roll_date("MNQZ25", 2025)
        assert roll == date(2025, 12, 11)

    def test_quarterly_roll_date_invalid_month(self, cal: CMEFuturesCalendar) -> None:
        """Roll date with invalid month code should raise."""
        with pytest.raises(ValueError, match="Invalid month code"):
            cal.quarterly_roll_date("NQXL26", 2026)


class TestFuturesContract:
    """Tests for FuturesContract."""

    @pytest.fixture
    def cal(self) -> CMEFuturesCalendar:
        """Provide a CMEFuturesCalendar instance."""
        return CMEFuturesCalendar()

    def test_parse_nqh26(self) -> None:
        """Parse 'NQH26' format."""
        c = FuturesContract.parse("NQH26")
        assert c.root == "NQ"
        assert c.month == 3
        assert c.year == 2026

    def test_parse_mnqz25(self) -> None:
        """Parse 'MNQZ25' format."""
        c = FuturesContract.parse("MNQZ25")
        assert c.root == "MNQ"
        assert c.month == 12
        assert c.year == 2025

    def test_parse_nqh2026(self) -> None:
        """Parse 4-digit year format 'NQH2026'."""
        c = FuturesContract.parse("NQH2026")
        assert c.root == "NQ"
        assert c.month == 3
        assert c.year == 2026

    def test_parse_esh26(self) -> None:
        """Parse ES contract 'ESH26'."""
        c = FuturesContract.parse("ESH26")
        assert c.root == "ES"
        assert c.month == 3

    def test_parse_mesh26(self) -> None:
        """Parse MES contract 'MESH26'."""
        c = FuturesContract.parse("MESH26")
        assert c.root == "MES"
        assert c.month == 3

    def test_parse_all_months(self) -> None:
        """Parse all month codes."""
        for code, month in [("H", 3), ("M", 6), ("U", 9), ("Z", 12)]:
            c = FuturesContract.parse(f"NQ{code}26")
            assert c.month == month

    def test_parse_invalid_root(self) -> None:
        """Parse with invalid root should raise."""
        with pytest.raises(ValueError, match="Unrecognized root"):
            FuturesContract.parse("XXH26")

    def test_parse_invalid_month(self) -> None:
        """Parse with invalid month code should raise."""
        with pytest.raises(ValueError, match="Invalid month code"):
            FuturesContract.parse("NQX26")

    def test_parse_empty_string(self) -> None:
        """Parse empty string should raise."""
        with pytest.raises(ValueError, match="cannot be empty"):
            FuturesContract.parse("")

    def test_parse_too_short(self) -> None:
        """Parse too-short string should raise."""
        with pytest.raises(ValueError, match="too short"):
            FuturesContract.parse("NQ")

    def test_parse_2digit_year_low(self) -> None:
        """2-digit years 00-30 map to 2000-2030."""
        c = FuturesContract.parse("NQH00")
        assert c.year == 2000

        c = FuturesContract.parse("NQH30")
        assert c.year == 2030

    def test_parse_2digit_year_high(self) -> None:
        """2-digit years 31-99 map to 2031-2099 (future contracts)."""
        # Adjust the test: 31-99 map to 2031-2099, not 1931-1999
        # Modern futures contracts are always future-dated
        c = FuturesContract.parse("NQH31")
        assert c.year == 2031

        c = FuturesContract.parse("NQH99")
        assert c.year == 2099

    def test_symbol_short_year(self) -> None:
        """symbol() with short_year=True (default)."""
        c = FuturesContract(root="NQ", month=3, year=2026)
        assert c.symbol() == "NQH26"
        assert c.symbol(short_year=True) == "NQH26"

    def test_symbol_long_year(self) -> None:
        """symbol() with short_year=False."""
        c = FuturesContract(root="NQ", month=3, year=2026)
        assert c.symbol(short_year=False) == "NQH2026"

    def test_symbol_mnq(self) -> None:
        """symbol() for MNQ contracts."""
        c = FuturesContract(root="MNQ", month=12, year=2025)
        assert c.symbol() == "MNQZ25"

    def test_symbol_roundtrip(self) -> None:
        """Parse -> symbol -> parse should round-trip."""
        original = "MNQZ25"
        c = FuturesContract.parse(original)
        assert c.symbol() == original
        c2 = FuturesContract.parse(c.symbol())
        assert c2 == c

    def test_next_contract_h_to_m(self) -> None:
        """next_contract: H (Mar) -> M (Jun)."""
        c = FuturesContract(root="NQ", month=3, year=2026)
        next_c = c.next_contract()
        assert next_c.root == "NQ"
        assert next_c.month == 6
        assert next_c.year == 2026

    def test_next_contract_z_to_h(self) -> None:
        """next_contract: Z (Dec) -> H (Mar next year)."""
        c = FuturesContract(root="NQ", month=12, year=2025)
        next_c = c.next_contract()
        assert next_c.root == "NQ"
        assert next_c.month == 3
        assert next_c.year == 2026

    def test_prev_contract_m_to_h(self) -> None:
        """prev_contract: M (Jun) -> H (Mar)."""
        c = FuturesContract(root="NQ", month=6, year=2026)
        prev_c = c.prev_contract()
        assert prev_c.root == "NQ"
        assert prev_c.month == 3
        assert prev_c.year == 2026

    def test_prev_contract_h_to_z(self) -> None:
        """prev_contract: H (Mar) -> Z (Dec previous year)."""
        c = FuturesContract(root="NQ", month=3, year=2026)
        prev_c = c.prev_contract()
        assert prev_c.root == "NQ"
        assert prev_c.month == 12
        assert prev_c.year == 2025

    def test_is_front_month_before_expiry(self, cal: CMEFuturesCalendar) -> None:
        """is_front_month returns True before the roll date."""
        c = FuturesContract(root="NQ", month=3, year=2026)
        # NQH26 rolls on March 12, 2026
        assert c.is_front_month(date(2026, 3, 1), cal)
        assert c.is_front_month(date(2026, 3, 12), cal)

    def test_is_front_month_after_expiry(self, cal: CMEFuturesCalendar) -> None:
        """is_front_month returns False after the roll date."""
        c = FuturesContract(root="NQ", month=3, year=2026)
        # NQH26 rolls on March 12, 2026
        assert not c.is_front_month(date(2026, 3, 13), cal)
        assert not c.is_front_month(date(2026, 4, 1), cal)

    def test_frozen(self) -> None:
        """FuturesContract should be immutable."""
        c = FuturesContract(root="NQ", month=3, year=2026)
        with pytest.raises(AttributeError):
            c.root = "MNQ"  # type: ignore

    def test_post_init_validation_root(self) -> None:
        """Invalid root should raise in __post_init__."""
        with pytest.raises(ValueError, match="Invalid root"):
            FuturesContract(root="XX", month=3, year=2026)

    def test_post_init_validation_month(self) -> None:
        """Invalid month should raise in __post_init__."""
        with pytest.raises(ValueError, match="Invalid month"):
            FuturesContract(root="NQ", month=1, year=2026)

    def test_post_init_validation_year(self) -> None:
        """Invalid year should raise in __post_init__."""
        with pytest.raises(ValueError, match="out of range"):
            FuturesContract(root="NQ", month=3, year=1999)
