"""Python 3.10/3.14 compatibility shim for datetime.UTC."""
from __future__ import annotations

import datetime
import sys

if sys.version_info < (3, 11):
    # datetime.UTC was added in 3.11; backport it.
    datetime.UTC = datetime.timezone.utc  # type: ignore[attr-defined]
