"""Python 3.10 compatibility startup — monkey-patches datetime.UTC.

Set PYTHONSTARTUP=scripts/_compat_startup.py before running any
mnq_bot script on Python < 3.11.
"""

import datetime
import sys

if sys.version_info < (3, 11) and not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.UTC
