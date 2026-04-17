"""[REAL] Calibration harness for Layer-2 simulator parameters.

Currently exposes `fit_slippage` which fits per-regime OLS on shadow fills.
"""
from mnq.calibration.fit_slippage import (
    SlippageFit,
    SlippageModel,
    fit_per_regime,
    fit_slippage,
    regime_key,
)

__all__ = [
    "SlippageFit",
    "SlippageModel",
    "fit_per_regime",
    "fit_slippage",
    "regime_key",
]
