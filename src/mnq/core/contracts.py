"""[REAL] Futures contract identifier parsing and manipulation.

Supports NQ/MNQ/ES/MES quarterly (H/M/U/Z) contracts with 2-digit or 4-digit years.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from mnq.core.calendar import CMEFuturesCalendar

_MONTH_CODES: Final[dict[str, int]] = {"H": 3, "M": 6, "U": 9, "Z": 12}
_CODE_BY_MONTH: Final[dict[int, str]] = {v: k for k, v in _MONTH_CODES.items()}


@dataclass(frozen=True)
class FuturesContract:
    """A CME equity-index futures contract identifier.

    Attributes:
        root: "NQ" | "MNQ" | "ES" | "MES"
        month: 3, 6, 9, or 12
        year: 4-digit year
    """

    root: str
    month: int
    year: int

    def __post_init__(self) -> None:
        """Validate contract parameters."""
        if self.root not in ("NQ", "MNQ", "ES", "MES"):
            raise ValueError(f"Invalid root '{self.root}'; expected NQ|MNQ|ES|MES")
        if self.month not in (3, 6, 9, 12):
            raise ValueError(f"Invalid month {self.month}; expected 3|6|9|12")
        if not (2000 <= self.year <= 2100):
            raise ValueError(f"Year {self.year} out of range [2000, 2100]")

    @classmethod
    def parse(cls, symbol: str) -> FuturesContract:
        """Parse a futures contract symbol.

        Accepts formats like 'NQH26', 'MNQZ25', 'NQH2026'.
        Raises ValueError if the symbol is malformed.

        Args:
            symbol: Contract symbol string (case-sensitive).

        Returns:
            FuturesContract instance.

        Raises:
            ValueError: If symbol cannot be parsed.
        """
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("Contract symbol cannot be empty")

        # Determine root and month_code by examining the string structure
        # Possible patterns:
        #   NQH26, NQH2026 (2-letter root)
        #   MNQH26, MNQH2026 (3-letter root)
        #   ESH26, ESH2026 (2-letter root)
        #   MESH26, MESH2026 (3-letter root)

        root = None
        month_code = None
        year_str = None

        if len(symbol) < 4:
            raise ValueError(f"Symbol '{symbol}' too short (min 4 chars)")

        # Try 3-letter root (MNQ, MES)
        if len(symbol) >= 4 and symbol[:3] in ("MNQ", "MES"):
            root = symbol[:3]
            month_code = symbol[3]
            year_str = symbol[4:]
        # Try 2-letter root (NQ, ES)
        elif len(symbol) >= 4 and symbol[:2] in ("NQ", "ES"):
            root = symbol[:2]
            month_code = symbol[2]
            year_str = symbol[3:]
        else:
            raise ValueError(f"Unrecognized root in symbol '{symbol}'")

        if not month_code:
            raise ValueError(f"No month code found in symbol '{symbol}'")
        if month_code not in _MONTH_CODES:
            raise ValueError(
                f"Invalid month code '{month_code}' in symbol '{symbol}'; "
                f"expected H|M|U|Z"
            )

        if not year_str:
            raise ValueError(f"No year found in symbol '{symbol}'")

        # Parse year: 2-digit or 4-digit
        try:
            if len(year_str) == 2:
                year_2digit = int(year_str)
                # Convert 2-digit to 4-digit: all map to 2000-2099
                # (modern futures contracts are always current or future year)
                year = 2000 + year_2digit
            elif len(year_str) == 4:
                year = int(year_str)
            else:
                raise ValueError(
                    f"Year '{year_str}' must be 2 or 4 digits, got {len(year_str)}"
                )
        except ValueError as exc:
            raise ValueError(f"Could not parse year '{year_str}' in symbol '{symbol}'") from exc

        month = _MONTH_CODES[month_code]
        return cls(root=root, month=month, year=year)

    def symbol(self, short_year: bool = True) -> str:
        """Return the CME symbol string for this contract.

        Args:
            short_year: If True, use 2-digit year (e.g., 'NQH26');
                       if False, use 4-digit year (e.g., 'NQH2026').

        Returns:
            Symbol string like 'NQH26' or 'MNQZ25'.
        """
        month_code = _CODE_BY_MONTH[self.month]
        year_part = (
            str(self.year % 100).zfill(2) if short_year else str(self.year)
        )
        return f"{self.root}{month_code}{year_part}"

    def is_front_month(self, on: date, cal: CMEFuturesCalendar) -> bool:
        """Check if this contract is the front month on the given date.

        Front month is the earliest non-expired contract.

        Args:
            on: Check date.
            cal: CMEFuturesCalendar instance.

        Returns:
            True if this contract is the current front month.
        """
        # Get the roll date for this contract
        try:
            roll_date = cal.quarterly_roll_date(self.symbol(), self.year)
        except ValueError:
            return False

        # If we're before the roll date, this is the front month
        return bool(on <= roll_date)

    def next_contract(self) -> FuturesContract:
        """Return the next quarterly contract.

        H -> M -> U -> Z -> H (next year).

        Returns:
            FuturesContract for the next quarter.
        """
        month_order = [3, 6, 9, 12]
        current_idx = month_order.index(self.month)
        next_idx = (current_idx + 1) % 4
        next_month = month_order[next_idx]
        next_year = self.year if next_idx > current_idx else self.year + 1
        return FuturesContract(root=self.root, month=next_month, year=next_year)

    def prev_contract(self) -> FuturesContract:
        """Return the previous quarterly contract.

        H <- M <- U <- Z <- H (previous year).

        Returns:
            FuturesContract for the previous quarter.
        """
        month_order = [3, 6, 9, 12]
        current_idx = month_order.index(self.month)
        prev_idx = (current_idx - 1) % 4
        prev_month = month_order[prev_idx]
        prev_year = self.year if prev_idx < current_idx else self.year - 1
        return FuturesContract(root=self.root, month=prev_month, year=prev_year)
