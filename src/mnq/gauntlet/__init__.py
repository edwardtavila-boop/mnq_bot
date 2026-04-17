"""[CONTRACT] The gauntlet — 14 gates, run in order, all must pass.

A candidate spec passes if and only if every gate returns passed=True.
Most candidates die at gates 1-3 (cheap filters). Gates 11-12 (parity,
capital-protection) are last because they're the most operationally
expensive to investigate when they fail.

GATE ORDER (must execute in this sequence):

    1. gate_static       — schema valid, generators emit, hash matches
    2. gate_layer1       — vectorized backtest: positive expectancy, >100 trades
    3. gate_layer2       — event-driven: profit factor > 1.3, max DD < 8%
    4. gate_cpcv         — CPCV runs, median path Sharpe > 1.0, p25 > 0.4
    5. gate_dsr          — DSR > 0.95
    6. gate_psr          — PSR(1.0) > 0.90
    7. gate_regime       — 30+ trades per regime cell, 2-of-3 x 2-of-3 profitable
    8. gate_stress       — profitable at slippage 2x AND latency +200ms
    9. gate_perturb      — 80% of ±10/20% param perturbations stay profitable
   10. gate_layer25      — Python executor + mock WS integration test passes
   11. gate_parity       — Pine vs. Python signal parity on 30-day reference set
   12. gate_capital      — bootstrap 99th-percentile DD fits within per_week_max_loss * 4
   13. gate_alpha        — alpha significant against cash AND mnq_intraday AND naive_momentum
   14. gate_beta         — |beta| < 0.3 against mnq_intraday

NEW (this commit): gates 13 and 14 are the attribution gates.
"""
from __future__ import annotations

GATE_ORDER: tuple[str, ...] = (
    "gate_static",
    "gate_layer1",
    "gate_layer2",
    "gate_cpcv",
    "gate_dsr",
    "gate_psr",
    "gate_regime",
    "gate_stress",
    "gate_perturb",
    "gate_layer25",
    "gate_parity",
    "gate_capital",
    "gate_alpha",
    "gate_beta",
)
