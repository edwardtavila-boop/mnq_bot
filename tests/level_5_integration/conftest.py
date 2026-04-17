"""Level-5 integration tests — real I/O. Skipped if credentials are missing."""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    level5 = pytest.mark.level5
    for item in items:
        if "level_5_integration" in str(item.fspath):
            item.add_marker(level5)
