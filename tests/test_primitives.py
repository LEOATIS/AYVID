"""Numeric-parity pin tests for execution_engine.math._primitives.

These tests lock down bit-exact outputs against seeded NumPy inputs. If
any primitive's numeric behaviour changes, these tests fail immediately —
preventing silent drift in the YC walk-forward / scenario numbers.

Golden values were captured on 2026-04-23 from the pre-Phase-2 canonical
implementations (`signals.compute_hurst_exponent`, `signals.compute_rsi`,
`signals.compute_atr`, `regime.compute_realized_volatility`, etc.).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from execution_engine.math import _primitives as prims

# ── Deterministic fixtures ────────────────────────────────────


@pytest.fixture(scope="module")
def trending_series() -> np.ndarray:
    """Log-returns from a drifted GBM — should produce Hurst > 0.5."""
    rng = np.random.default_rng(seed=42)
    returns = rng.normal(loc=0.0005, scale=0.01, size=120) + np.linspace(0, 0.002, 120)
    return returns.astype(np.float64)


@pytest.fixture(scope="module")
def mean_reverting_series() -> np.ndarray:
    """Log-returns of an OU-like mean-reverting series."""
    rng = np.random.default_rng(seed=7)
    x = np.zeros(120, dtype=np.float64)
    for i in range(1, 120):
        x[i] = 0.4 * x[i - 1] + rng.normal(scale=0.01)
    return x


@pytest.fixture(scope="module")
def prices_gbm() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Geometric-Brownian-motion close prices plus synthetic HLV."""
    rng = np.random.default_rng(seed=123)
    returns = rng.normal(loc=0.0003, scale=0.012, size=100)
    closes = 100.0 * np.exp(np.cumsum(returns))
    highs = closes * (1.0 + rng.uniform(0.0, 0.008, size=100))
    lows = closes * (1.0 - rng.uniform(0.0, 0.008, size=100))
    volumes = rng.lognormal(mean=14.0, sigma=0.3, size=100)
    return closes, highs, lows, volumes


# ── log_returns / annualize_vol ───────────────────────────────


def test_log_returns_matches_np_diff_log(prices_gbm):
    closes, *_ = prices_gbm
    result = prims.log_returns(closes)
    expected = np.diff(np.log(closes))
    np.testing.assert_array_equal(result, expected)
    assert result.shape == (len(closes) - 1,)


def test_annualize_vol_uses_252():
    assert math.isclose(prims.annualize_vol(0.01), 0.01 * math.sqrt(252))


# ── compute_realized_vol ──────────────────────────────────────


def test_realized_vol_pins(trending_series):
    assert math.isclose(
        prims.compute_realized_vol(trending_series, window=20),
        0.134040746828323,
        rel_tol=1e-9,
    )
    assert math.isclose(
        prims.compute_realized_vol(trending_series, window=60),
        0.122237841277305,
        rel_tol=1e-9,
    )


def test_realized_vol_short_series_fallback():
    assert prims.compute_realized_vol(np.array([0.01, -0.02]), window=20) == pytest.approx(
        np.std(np.array([0.01, -0.02])) * math.sqrt(252),
        rel=1e-9,
    )
    assert prims.compute_realized_vol(np.array([]), window=20) == pytest.approx(0.2)


# ── rolling_zscore ────────────────────────────────────────────


def test_rolling_zscore_pins(prices_gbm):
    closes, *_ = prices_gbm
    z = prims.rolling_zscore(closes, lookback=30)
    assert math.isclose(z, 1.1587243387520423, rel_tol=1e-9)


def test_rolling_zscore_short_series_returns_zero():
    assert prims.rolling_zscore(np.array([1.0, 2.0]), lookback=30) == 0.0


def test_rolling_zscore_zero_std_guard():
    flat = np.ones(40, dtype=np.float64)
    assert prims.rolling_zscore(flat, lookback=30) == 0.0


# ── compute_hurst ─────────────────────────────────────────────


def test_hurst_pins_trending(trending_series):
    h = prims.compute_hurst(trending_series, max_lag=20)
    assert math.isclose(h, 0.8046085715778936, rel_tol=1e-9)


def test_hurst_pins_mean_reverting(mean_reverting_series):
    h = prims.compute_hurst(mean_reverting_series, max_lag=20)
    assert math.isclose(h, 0.8747999424277138, rel_tol=1e-9)


def test_hurst_short_series_returns_0_5():
    short = np.random.default_rng(0).normal(size=10)
    assert prims.compute_hurst(short, max_lag=20) == 0.5


def test_hurst_clipped_to_unit_interval(mean_reverting_series):
    h = prims.compute_hurst(mean_reverting_series)
    assert 0.0 <= h <= 1.0


# ── compute_rsi ───────────────────────────────────────────────


def test_rsi_pins_gbm(prices_gbm):
    closes, *_ = prices_gbm
    rsi = prims.compute_rsi(closes, period=14)
    assert math.isclose(rsi, 57.57794911225191, rel_tol=1e-9)


def test_rsi_short_series_returns_50():
    assert prims.compute_rsi(np.array([1.0, 2.0]), period=14) == 50.0


def test_rsi_monotonic_up_returns_100():
    rising = np.linspace(1.0, 2.0, 30)
    assert prims.compute_rsi(rising, period=14) == 100.0


# ── compute_atr ───────────────────────────────────────────────


def test_atr_pins_gbm(prices_gbm):
    closes, highs, lows, _ = prices_gbm
    atr = prims.compute_atr(highs, lows, closes, period=14)
    assert math.isclose(atr, 1.4086007991930083, rel_tol=1e-9)


def test_atr_short_series_uses_range_mean():
    highs = np.array([101.0, 102.0, 103.0])
    lows = np.array([99.0, 100.0, 101.0])
    closes = np.array([100.0, 101.0, 102.0])
    assert prims.compute_atr(highs, lows, closes, period=14) == pytest.approx(2.0)


# ── compute_volume_profile ────────────────────────────────────


def test_volume_profile_pins(prices_gbm):
    _, _, _, volumes = prices_gbm
    ratio = prims.compute_volume_profile(volumes, lookback=20)
    assert math.isclose(ratio, 0.9478802973334707, rel_tol=1e-9)


def test_volume_profile_flat_returns_1():
    flat = np.ones(30, dtype=np.float64)
    assert prims.compute_volume_profile(flat, lookback=20) == 1.0


# ── compute_trend_strength ────────────────────────────────────


def test_trend_strength_pins_gbm(prices_gbm):
    closes, *_ = prices_gbm
    t = prims.compute_trend_strength(closes, short=10, long=30)
    assert math.isclose(t, 0.8052338616432299, rel_tol=1e-9)


def test_trend_strength_flat_returns_zero():
    flat = np.ones(40, dtype=np.float64)
    assert prims.compute_trend_strength(flat, short=10, long=30) == 0.0


def test_trend_strength_bounded_unit_interval(prices_gbm):
    closes, *_ = prices_gbm
    t = prims.compute_trend_strength(closes)
    assert -1.0 <= t <= 1.0


# ── estimate_ou_parameters ────────────────────────────────────


def test_ou_parameters_return_three_floats(prices_gbm):
    closes, *_ = prices_gbm
    theta, mu, sigma = prims.estimate_ou_parameters(closes)
    assert isinstance(theta, float)
    assert isinstance(mu, float)
    assert isinstance(sigma, float)
    assert theta >= 0.0
    assert sigma >= 0.0


def test_ou_parameters_short_series_fallback():
    theta, mu, sigma = prims.estimate_ou_parameters(np.array([100.0, 101.0]))
    assert theta == 0.0
    assert sigma == 0.2


# ── bayesian_conviction_update ────────────────────────────────


def test_bayesian_conviction_neutral_prior_no_factors_returns_prior():
    assert prims.bayesian_conviction_update(0.5, []) == pytest.approx(0.5)


def test_bayesian_conviction_raises_confidence_on_positive_lr():
    out = prims.bayesian_conviction_update(0.5, [(2.0, 1.0)])
    assert 0.5 < out < 1.0


def test_bayesian_conviction_lowers_confidence_on_sub_unity_lr():
    out = prims.bayesian_conviction_update(0.5, [(0.3, 1.0)])
    assert 0.0 < out < 0.5


def test_bayesian_conviction_clamps_extreme_prior():
    out = prims.bayesian_conviction_update(1.5, [(1.0, 1.0)])
    assert 0.0 <= out <= 1.0
    out2 = prims.bayesian_conviction_update(-0.2, [(1.0, 1.0)])
    assert 0.0 <= out2 <= 1.0


def test_bayesian_conviction_pins_known_combination():
    """Pin test: specific prior + factor list must produce exact value."""
    out = prims.bayesian_conviction_update(0.5, [(2.0, 1.0), (1.5, 0.5), (0.8, 0.3)])
    assert math.isclose(out, 0.6961300471897319, rel_tol=1e-9)


# ── Cross-parity: primitives ↔ signals/regime public API ───────
# These tests are the load-bearing safety net for Step 5 of the Phase-2
# refactor. `signals.py` and `regime.py` now re-export from
# `_primitives.py`; this check proves the public API still resolves to
# the identical numeric output on seeded inputs. If any of these fail,
# either the re-export was broken or a numeric drift was introduced.


def test_primitives_match_signals_public_api(trending_series, prices_gbm):
    """_primitives ↔ signals.py bit-exact parity on seeded inputs."""
    from execution_engine.math import signals

    closes, highs, lows, volumes = prices_gbm

    assert prims.compute_hurst(trending_series, 20) == signals.compute_hurst_exponent(
        trending_series, 20
    )
    assert prims.compute_rsi(closes, 14) == signals.compute_rsi(closes, 14)
    assert prims.compute_atr(highs, lows, closes, 14) == signals.compute_atr(
        highs, lows, closes, 14
    )
    assert prims.compute_volume_profile(volumes, 20) == signals.compute_volume_profile(volumes, 20)
    assert prims.compute_trend_strength(closes, 10, 30) == signals.compute_trend_strength(
        closes, 10, 30
    )

    p_ou = prims.estimate_ou_parameters(closes)
    s_ou = signals.estimate_ou_parameters(closes)
    assert p_ou == s_ou

    factors = [(2.0, 1.0), (1.5, 0.5), (0.8, 0.3)]
    assert prims.bayesian_conviction_update(0.5, factors) == signals.bayesian_conviction_update(
        0.5, factors
    )


def test_primitives_match_regime_realized_volatility(trending_series):
    """_primitives.compute_realized_vol ↔ regime.compute_realized_volatility."""
    from execution_engine.math import regime

    for window in (10, 20, 30, 60):
        assert prims.compute_realized_vol(
            trending_series, window
        ) == regime.compute_realized_volatility(trending_series, window)
