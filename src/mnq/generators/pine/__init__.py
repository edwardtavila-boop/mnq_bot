"""[REAL] Pine v6 generator — public surface.

render_pine(spec) -> str
    Deterministic Pine v6 source for the given StrategySpec.

static_check_pine(src) -> None
    Raises PineStaticCheckError on forbidden patterns
    (lookahead_on, raw security(, etc.).
"""

from __future__ import annotations

from mnq.generators.pine.generator import (
    PineGenerationError,
    PineStaticCheckError,
    render_pine,
    static_check_pine,
)

__all__ = [
    "PineGenerationError",
    "PineStaticCheckError",
    "render_pine",
    "static_check_pine",
]
