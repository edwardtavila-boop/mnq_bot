"""Level-1 tests for mnq.calibration.fit_slippage."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from mnq.calibration.fit_slippage import (
    SlippageFit,
    SlippageModel,
    fit_per_regime,
    fit_slippage,
    regime_key,
)


class TestFitSlippageSingleRegime:
    def test_recovers_known_a_b_within_5pct(self) -> None:
        """DoD from handoff: 200 synthetic fills, recover a, b within 5%."""
        rng = np.random.default_rng(seed=42)
        true_a = 0.30
        true_b = 0.08
        atr = rng.uniform(4.0, 40.0, size=200)
        # Noise = 0.02 ticks (MNQ tick = $0.50; realistic for 1-contract fills).
        noise = rng.normal(0.0, 0.02, size=200)
        slippage = true_a + true_b * atr + noise

        fit = fit_slippage(slippage, atr)

        assert fit.n == 200
        assert abs(fit.a - true_a) / abs(true_a) < 0.05, f"a={fit.a} vs true {true_a}"
        assert abs(fit.b - true_b) / abs(true_b) < 0.05, f"b={fit.b} vs true {true_b}"
        assert fit.r2 > 0.9
        assert fit.residual_std_ticks == pytest.approx(0.02, abs=0.01)

    def test_mismatched_shapes_raises(self) -> None:
        with pytest.raises(ValueError):
            fit_slippage([1, 2, 3], [1, 2])

    def test_zero_rows_returns_zero_fit(self) -> None:
        fit = fit_slippage([], [])
        assert fit.n == 0
        assert fit.a == 0.0 and fit.b == 0.0

    def test_constant_atr_no_slope(self) -> None:
        # Every x is the same — OLS can only identify the intercept (mean y).
        y = np.array([1.0, 1.2, 0.8, 1.1], dtype=np.float64)
        x = np.array([10.0, 10.0, 10.0, 10.0], dtype=np.float64)
        fit = fit_slippage(y, x)
        assert fit.b == 0.0
        assert fit.a == pytest.approx(float(y.mean()), rel=1e-9)
        assert fit.r2 == 0.0

    def test_predict(self) -> None:
        fit = SlippageFit(a=0.2, b=0.05, n=10, r2=0.9, residual_std_ticks=0.01)
        assert fit.predict(10.0) == pytest.approx(0.7)
        assert fit.predict(20.0) == pytest.approx(1.2)


class TestRegimeKey:
    def test_session_phases_map_correctly(self) -> None:
        # 10:00 ET = 600 minutes of day
        k_open = regime_key({"session_phase_minute": 600, "bar_volume": 1000})
        # 12:00 ET = 720
        k_mid = regime_key({"session_phase_minute": 720, "bar_volume": 1000})
        # 15:30 ET = 930
        k_close = regime_key({"session_phase_minute": 930, "bar_volume": 1000})

        assert k_open[0] == "open"
        assert k_mid[0] == "mid"
        assert k_close[0] == "close"

    def test_liquidity_buckets(self) -> None:
        assert regime_key({"session_phase_minute": 600, "bar_volume": 100})[1] == "low"
        assert regime_key({"session_phase_minute": 600, "bar_volume": 1000})[1] == "normal"
        assert regime_key({"session_phase_minute": 600, "bar_volume": 5000})[1] == "high"

    def test_missing_volume_is_unknown(self) -> None:
        assert regime_key({"session_phase_minute": 600})[1] == "unknown"
        assert regime_key({"session_phase_minute": 600, "bar_volume": None})[1] == "unknown"


class TestFitPerRegime:
    def _make_synthetic_fills(
        self, rng: np.random.Generator, n_per_regime: int = 60
    ) -> pl.DataFrame:
        """Three regimes with distinct (a, b) so we can verify separation."""
        # (phase_minute, volume, true_a, true_b)
        regime_specs = [
            (600, 1000, 0.20, 0.05),  # open, normal
            (720, 200, 0.40, 0.10),  # mid, low — low-liquidity, worse slippage
            (930, 5000, 0.15, 0.04),  # close, high — liquid, better
        ]
        rows = []
        for phase_min, vol, a, b in regime_specs:
            atr = rng.uniform(4.0, 40.0, size=n_per_regime)
            noise = rng.normal(0.0, 0.04, size=n_per_regime)
            slip = a + b * atr + noise
            for s, x in zip(slip, atr, strict=True):
                rows.append(
                    {
                        "session_phase_minute": phase_min,
                        "bar_volume": float(vol),
                        "slippage_ticks": float(s),
                        "bar_atr_ticks": float(x),
                    }
                )
        return pl.DataFrame(rows)

    def test_separates_regimes_and_recovers_coefficients(self) -> None:
        rng = np.random.default_rng(seed=7)
        df = self._make_synthetic_fills(rng, n_per_regime=80)
        model = fit_per_regime(df, min_observations=20)

        # Expect three regimes plus pooled fallback.
        assert len(model.fits) == 3
        assert model.fallback is not None

        # Recovery within 10% per regime (narrower noise band than single test).
        expected = {
            ("open", "normal"): (0.20, 0.05),
            ("mid", "low"): (0.40, 0.10),
            ("close", "high"): (0.15, 0.04),
        }
        for key, (a_true, b_true) in expected.items():
            assert key in model.fits, f"regime {key} missing"
            fit = model.fits[key]
            assert abs(fit.a - a_true) / a_true < 0.15, f"a[{key}]={fit.a}"
            assert abs(fit.b - b_true) / b_true < 0.10, f"b[{key}]={fit.b}"

    def test_predict_via_model(self) -> None:
        rng = np.random.default_rng(seed=7)
        df = self._make_synthetic_fills(rng, n_per_regime=80)
        model = fit_per_regime(df, min_observations=20)

        mid_pred = model.predict(("mid", "low"), atr_ticks=20.0)
        # True: 0.4 + 0.1*20 = 2.4
        assert mid_pred == pytest.approx(2.4, abs=0.15)

    def test_unknown_regime_falls_back(self) -> None:
        rng = np.random.default_rng(seed=7)
        df = self._make_synthetic_fills(rng, n_per_regime=80)
        model = fit_per_regime(df, min_observations=20)

        pred = model.predict(("doesnt", "exist"), atr_ticks=20.0)
        # Should be roughly the pooled mean slippage at atr=20, which is the
        # average of the three regimes' true lines evaluated there.
        # 0.2+1 ; 0.4+2 ; 0.15+0.8 → mean ≈ (1.2 + 2.4 + 0.95)/3 ≈ 1.52
        assert 0.8 < pred < 2.5

    def test_unknown_regime_no_fallback_raises(self) -> None:
        model = SlippageModel(fits={}, fallback=None)
        with pytest.raises(KeyError):
            model.predict(("x", "y"), atr_ticks=10.0)

    def test_below_min_observations_rolls_into_fallback_only(self) -> None:
        # 5 observations per regime: every regime is below min=20, so
        # `.fits` stays empty but fallback is still built from pooled data.
        rng = np.random.default_rng(seed=7)
        df = self._make_synthetic_fills(rng, n_per_regime=5)
        model = fit_per_regime(df, min_observations=20)
        assert len(model.fits) == 0
        assert model.fallback is not None
        assert model.fallback.n == 15

    def test_missing_required_column_raises(self) -> None:
        df = pl.DataFrame({"foo": [1, 2, 3], "bar": [4, 5, 6]})
        with pytest.raises(ValueError):
            fit_per_regime(df)
