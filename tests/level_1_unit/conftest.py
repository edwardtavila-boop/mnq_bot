"""Level-1 unit tests: pure, no I/O, no network. Tag everything with `level1`."""

from __future__ import annotations

import pytest


# Apply the `level1` marker automatically to every test in this directory,
# so `pytest -m level1` picks them all up without requiring each file to
# decorate explicitly.
def pytest_collection_modifyitems(config, items):  # noqa: D401
    level1 = pytest.mark.level1
    for item in items:
        if "level_1_unit" in str(item.fspath):
            item.add_marker(level1)
