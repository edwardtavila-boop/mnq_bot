"""[REAL] Microstructure features derivable from OHLCV bars.

These features extract higher-order signal from bar tape when a level-2
depth feed is not available. Each follows the same streaming contract as
mnq.features.ema / rvol / vwap:

    f = Feature(length=...)
    for bar in bars:
        f.update(bar)    # returns the new value or None
    f.value              # last value, or None if not ready
    f.ready              # True once warm

Rationale:
    * **BarImbalance** — close location in range z-score. Classical tape-
      reading proxy for intra-bar buyer / seller dominance. Pine
      analog: `(2*close - high - low) / (high - low)`.
    * **VolumeEntropy** — Shannon entropy of normalized volume over a
      rolling window. Low entropy = clumpy (news / sweeps), high entropy
      = diffuse (random flow). Used to filter out noise-dominated regimes.
    * **LiquidityAbsorption** — z-score of `volume / range`. Flags bars
      where heavy volume moved price very little (liquidity absorbed at
      a level) vs bars where thin volume moved price a lot (void). This
      is a canonical "iceberg" / absorption signal.
    * **BarReturnAutocorrelation** — lag-1 autocorrelation of log returns
      over a rolling window. Negative autocorr ⇒ bid-ask bounce / mean
      reversion; positive ⇒ trending / momentum. Used as a regime prior
      by the gate layer.

All four operate in float64 and return None until warm. None of them
quantize to ticks — they are pure signal-space features. The calling
scorer is responsible for thresholding.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime

from mnq.core.types import Bar

# -----------------------------------------------------------------------
# Numerical constants
# -----------------------------------------------------------------------
# Minimum range in ticks below which we treat the bar as zero-range.
# MNQ tick size = 0.25; we use 0.125 (half a tick) as a float guard.
_RANGE_EPS: float = 0.125


# -----------------------------------------------------------------------
# C1 — BarImbalance
# -----------------------------------------------------------------------
class BarImbalance:
    """Close-location-in-range z-score over a rolling window.

    Raw signal per bar:
        raw = (2*close - high - low) / (high - low)    in [-1, +1]
        raw = 0 if high == low (doji; no directional info)

    Output: z-score of ``raw`` over the last ``length`` bars. ``ready``
    turns True on the first bar after the window fills.

    Interpretation:
        * +2σ: a strong up-bar relative to the recent distribution.
        * -2σ: a strong down-bar.
        *  0 : typical bar for the current regime.
    """

    __slots__ = ("length", "_buf", "_sum", "_sum_sq", "_last_raw", "_value", "_last_update_bar_ts")

    def __init__(self, length: int = 50) -> None:
        if length < 5:
            raise ValueError("BarImbalance length must be >= 5")
        self.length = int(length)
        self._buf: deque[float] = deque(maxlen=self.length)
        self._sum: float = 0.0
        self._sum_sq: float = 0.0
        self._last_raw: float = 0.0
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    @staticmethod
    def _raw(bar: Bar) -> float:
        hi = float(bar.high)
        lo = float(bar.low)
        cl = float(bar.close)
        rng = hi - lo
        if rng < _RANGE_EPS:
            return 0.0
        return (2.0 * cl - hi - lo) / rng

    def update(self, bar: Bar) -> float | None:
        self._last_update_bar_ts = bar.ts
        raw = self._raw(bar)
        self._last_raw = raw
        if len(self._buf) == self.length:
            old = self._buf[0]
            self._sum -= old
            self._sum_sq -= old * old
        self._buf.append(raw)
        self._sum += raw
        self._sum_sq += raw * raw
        if len(self._buf) >= self.length:
            n = float(self.length)
            mean = self._sum / n
            var = max(self._sum_sq / n - mean * mean, 0.0)
            std = math.sqrt(var)
            self._value = (raw - mean) / std if std > 0.0 else 0.0
        return self._value

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def raw(self) -> float:
        """The unnormalized imbalance for the last bar (in [-1, +1])."""
        return self._last_raw

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def last_update_bar_ts(self) -> datetime | None:
        return self._last_update_bar_ts


# -----------------------------------------------------------------------
# C2 — VolumeEntropy
# -----------------------------------------------------------------------
class VolumeEntropy:
    """Shannon entropy of normalized volume over a rolling window.

    Let v_i be the volume of bar i and p_i = v_i / sum(v). Entropy is

        H = -sum(p_i * log(p_i))    (natural log)

    Normalized output:
        H_norm = H / log(length)    in [0, 1]

    Interpretation:
        * 1.0: perfectly uniform volume across the window — diffuse flow.
        * 0.5: typical mixed activity.
        * 0.0: one bar dominated ⇒ news spike, sweep, or flash move.

    If the window sum is 0 the feature returns 1.0 (maximally uniform,
    i.e. no information).
    """

    __slots__ = ("length", "_buf", "_sum", "_value", "_last_update_bar_ts", "_log_length")

    def __init__(self, length: int = 30) -> None:
        if length < 3:
            raise ValueError("VolumeEntropy length must be >= 3")
        self.length = int(length)
        self._buf: deque[float] = deque(maxlen=self.length)
        self._sum: float = 0.0
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None
        self._log_length: float = math.log(self.length)

    def update(self, bar: Bar) -> float | None:
        v = max(float(bar.volume), 0.0)
        self._last_update_bar_ts = bar.ts
        if len(self._buf) == self.length:
            self._sum -= self._buf[0]
        self._buf.append(v)
        self._sum += v
        if len(self._buf) < self.length:
            return self._value
        total = self._sum
        if total <= 0.0:
            self._value = 1.0
            return self._value
        h = 0.0
        for x in self._buf:
            if x <= 0.0:
                continue
            p = x / total
            h -= p * math.log(p)
        self._value = h / self._log_length
        return self._value

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def last_update_bar_ts(self) -> datetime | None:
        return self._last_update_bar_ts


# -----------------------------------------------------------------------
# C3 — LiquidityAbsorption
# -----------------------------------------------------------------------
class LiquidityAbsorption:
    """Z-score of ``volume / range`` over a rolling window.

    Raw signal per bar:
        raw = volume / max(high - low, _RANGE_EPS)

    Output: z-score of ``raw`` over the last ``length`` bars.

    Interpretation:
        * +2σ: huge volume absorbed in a tight range — stacked liquidity
          at a level, typical of an accumulation zone.
        * -2σ: thin volume moved price a lot — a void, typical of after-
          hours / gap zones.
        *  0 : regime-typical tape.
    """

    __slots__ = ("length", "_buf", "_sum", "_sum_sq", "_last_raw", "_value", "_last_update_bar_ts")

    def __init__(self, length: int = 50) -> None:
        if length < 5:
            raise ValueError("LiquidityAbsorption length must be >= 5")
        self.length = int(length)
        self._buf: deque[float] = deque(maxlen=self.length)
        self._sum: float = 0.0
        self._sum_sq: float = 0.0
        self._last_raw: float = 0.0
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    @staticmethod
    def _raw(bar: Bar) -> float:
        rng = max(float(bar.high) - float(bar.low), _RANGE_EPS)
        return float(bar.volume) / rng

    def update(self, bar: Bar) -> float | None:
        self._last_update_bar_ts = bar.ts
        raw = self._raw(bar)
        self._last_raw = raw
        if len(self._buf) == self.length:
            old = self._buf[0]
            self._sum -= old
            self._sum_sq -= old * old
        self._buf.append(raw)
        self._sum += raw
        self._sum_sq += raw * raw
        if len(self._buf) >= self.length:
            n = float(self.length)
            mean = self._sum / n
            var = max(self._sum_sq / n - mean * mean, 0.0)
            std = math.sqrt(var)
            self._value = (raw - mean) / std if std > 0.0 else 0.0
        return self._value

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def raw(self) -> float:
        """The unnormalized volume/range ratio for the last bar."""
        return self._last_raw

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def last_update_bar_ts(self) -> datetime | None:
        return self._last_update_bar_ts


# -----------------------------------------------------------------------
# C4 — BarReturnAutocorrelation
# -----------------------------------------------------------------------
class BarReturnAutocorrelation:
    """Lag-1 autocorrelation of log returns over a rolling window.

    For each bar we compute ``r_i = log(close_i) - log(close_{i-1})``,
    then compute lag-1 autocorrelation of the last ``length`` returns:

        rho = sum((r_i - mean)*(r_{i-1} - mean)) / sum((r_i - mean)^2)

    Interpretation:
        * +1: perfect trending (every return echoes the prior).
        *  0: no serial dependence.
        * -1: perfect mean reversion / bid-ask bounce.

    In practice on futures tape we see -0.2 .. +0.2. A persistent
    negative value is a classic mean-reverting regime indicator; a
    persistent positive value is a momentum regime.
    """

    __slots__ = (
        "length",
        "_ret_buf",
        "_prev_close",
        "_value",
        "_last_update_bar_ts",
    )

    def __init__(self, length: int = 50) -> None:
        if length < 10:
            raise ValueError("BarReturnAutocorrelation length must be >= 10")
        self.length = int(length)
        # We need `length` returns → `length+1` closes to seed.
        self._ret_buf: deque[float] = deque(maxlen=self.length)
        self._prev_close: float | None = None
        self._value: float | None = None
        self._last_update_bar_ts: datetime | None = None

    def update(self, bar: Bar) -> float | None:
        self._last_update_bar_ts = bar.ts
        cl = float(bar.close)
        if cl <= 0.0:
            # Log undefined. Treat as a no-op return but track close.
            self._prev_close = cl
            return self._value
        if self._prev_close is None or self._prev_close <= 0.0:
            self._prev_close = cl
            return self._value
        r = math.log(cl) - math.log(self._prev_close)
        self._prev_close = cl
        self._ret_buf.append(r)
        if len(self._ret_buf) < self.length:
            return self._value

        # Compute lag-1 autocorrelation.
        rets = list(self._ret_buf)
        n = len(rets)
        mean = sum(rets) / n
        num = 0.0
        denom = 0.0
        for i in range(n):
            dx = rets[i] - mean
            denom += dx * dx
            if i > 0:
                dy = rets[i - 1] - mean
                num += dx * dy
        self._value = (num / denom) if denom > 0.0 else 0.0
        return self._value

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def last_update_bar_ts(self) -> datetime | None:
        return self._last_update_bar_ts


__all__ = [
    "BarImbalance",
    "BarReturnAutocorrelation",
    "LiquidityAbsorption",
    "VolumeEntropy",
]
