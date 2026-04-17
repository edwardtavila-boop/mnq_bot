"""Level-2 property tests. Tag everything with `level2`."""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: D401
    level2 = pytest.mark.level2
    for item in items:
        if "level_2_property" in str(item.fspath):
            item.add_marker(level2)
