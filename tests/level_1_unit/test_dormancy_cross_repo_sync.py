"""A5 closure (operator session 2026-04-25 polish): cross-repo
DORMANT_BROKERS sync test.

Per the operator-wide mandate (CLAUDE.md, 2026-04-24): IBKR + Tastytrade
are active futures brokers; Tradovate is DORMANT. The mandate is
enforced in ``eta_engine/venues/router.py::DORMANT_BROKERS`` and
``mnq_bot/src/mnq/venues/dormancy.py::DORMANT_BROKERS``.

The two-project mandate (CLAUDE.md, 2026-04-17 lock) explicitly forbids
import-level consolidation between ``eta_engine`` and ``mnq_bot``.
That rules out making the two ``DORMANT_BROKERS`` constants come from a
single shared package -- but it does NOT rule out asserting they stay
in sync via filesystem inspection.

This test runs ONLY when ``eta_engine`` is co-located on the
operator's machine at the canonical path
(``C:/Users/edwar/OneDrive/Desktop/Base/eta_engine``). On any other
machine -- CI runners, fresh laptops, contributor forks -- the test
skips gracefully. The skip is intentional: it mirrors the two-project
no-consolidation rule.

When co-located, the test parses the OTHER repo's ``DORMANT_BROKERS``
literal from source (no import-level coupling) and asserts the two
sets match. A divergence is the operator's signal that they updated
one repo's mandate but forgot the other.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mnq.venues.dormancy import DORMANT_BROKERS as MNQ_BOT_DORMANT

# Canonical co-location path on the operator's machine (Windows).
# The test SKIPS if this path doesn't exist -- it is operator-machine-
# local and not present on CI runners.
_ETA_ENGINE_REPO_CANDIDATES = [
    Path("C:/Users/edwar/OneDrive/Desktop/Base/eta_engine"),
    # Future: add Linux dev paths here if the operator works from WSL.
]


def _find_eta_engine() -> Path | None:
    """Return the eta_engine repo path if co-located, else None."""
    for cand in _ETA_ENGINE_REPO_CANDIDATES:
        if (cand / "venues" / "router.py").exists():
            return cand
    return None


def _parse_dormant_brokers(router_py: Path) -> frozenset[str]:
    """Parse DORMANT_BROKERS literal from eta_engine/venues/router.py.

    Uses regex rather than import to avoid creating a cross-repo
    import-time dependency that would violate the two-project rule.
    """
    text = router_py.read_text(encoding="utf-8")
    # Match: DORMANT_BROKERS = frozenset({"tradovate", ...})
    # or:    DORMANT_BROKERS: frozenset[str] = frozenset({"tradovate"})
    # or:    DORMANT_BROKERS = frozenset()  -- when dormancy lifts
    match = re.search(
        r"DORMANT_BROKERS\s*(?::\s*[^=]+)?=\s*frozenset\((.*?)\)",
        text,
        re.DOTALL,
    )
    if not match:
        msg = (
            "could not parse DORMANT_BROKERS in "
            f"{router_py}; the regex needs updating to match the "
            "current literal shape."
        )
        raise AssertionError(msg)
    inner = match.group(1).strip()
    if not inner:
        # frozenset() -- empty
        return frozenset()
    # Strip outer braces if present (frozenset({"a", "b"}))
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    # Split + dedupe + strip quotes
    items = {s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()}
    return frozenset(items)


class TestCrossRepoDormancySync:
    """Pin both repos to the same dormant-brokers set when co-located."""

    def test_sets_agree_when_eta_engine_is_co_located(self) -> None:
        apex_root = _find_eta_engine()
        if apex_root is None:
            pytest.skip(
                "eta_engine repo not co-located at "
                f"{_ETA_ENGINE_REPO_CANDIDATES[0]}; cross-repo "
                "sync test skipped (operator-machine-local check).",
            )
        router_py = apex_root / "venues" / "router.py"
        assert router_py.exists(), (
            f"eta_engine co-located at {apex_root} but router.py "
            "missing -- repo state inconsistent."
        )
        apex_dormant = _parse_dormant_brokers(router_py)
        assert apex_dormant == MNQ_BOT_DORMANT, (
            f"DORMANT_BROKERS mismatch:\n"
            f"  eta_engine/venues/router.py: {sorted(apex_dormant)}\n"
            f"  mnq_bot/src/mnq/venues/dormancy.py: {sorted(MNQ_BOT_DORMANT)}\n"
            f"\n"
            f"The operator-wide mandate requires both repos to track "
            f"the same dormant-broker set. Update both files in a "
            f"single operator action.\n"
            f"  eta_engine: {router_py}\n"
            f"  mnq_bot: src/mnq/venues/dormancy.py"
        )


class TestDormantBrokersParser:
    """Pin the parser regex against the formats both repos use."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                'DORMANT_BROKERS = frozenset({"tradovate"})',
                frozenset({"tradovate"}),
            ),
            (
                'DORMANT_BROKERS: frozenset[str] = frozenset({"tradovate"})',
                frozenset({"tradovate"}),
            ),
            (
                'DORMANT_BROKERS = frozenset({"tradovate", "ibkr"})',
                frozenset({"tradovate", "ibkr"}),
            ),
            (
                "DORMANT_BROKERS = frozenset()",
                frozenset(),
            ),
            (
                # Multi-line literal (eta_engine-style)
                'DORMANT_BROKERS: frozenset[str] = frozenset({\n    "tradovate",\n})',
                frozenset({"tradovate"}),
            ),
        ],
    )
    def test_parser_handles_known_shapes(
        self,
        tmp_path: Path,
        source: str,
        expected: frozenset[str],
    ) -> None:
        f = tmp_path / "router.py"
        f.write_text(f"# header\n{source}\n# trailer\n", encoding="utf-8")
        result = _parse_dormant_brokers(f)
        assert result == expected
