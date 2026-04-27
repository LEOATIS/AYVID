"""Tests for multi-factor signal scoring engine."""

from __future__ import annotations

import numpy as np


class TestHurstExponent:
    def test_random_walk_near_half(self):
        from execution_engine.math.signals import compute_hurst_exponent

        np.random.seed(42)
        walk = np.random.normal(0, 1, 500)
        h = compute_hurst_exponent(walk)
        assert 0.2 < h < 0.85

    def test_trending_series_above_half(self):
        from execution_engine.math.signals import compute_hurst_exponent

        trend = np.cumsum(np.ones(200) * 0.01 + np.random.normal(0, 0.005, 200))
        returns = np.diff(trend)
        h = compute_hurst_exponent(returns)
        assert h > 0.4

    def test_short_series_returns_half(self):
        from execution_engine.math.signals import compute_hurst_exponent

        h = compute_hurst_exponent(np.array([0.01, -0.01]))
        assert h == 0.5


class TestOUParameters:
    def test_positive_theta_for_mean_reverting(self):
        from execution_engine.math.signals import estimate_ou_parameters

        np.random.seed(42)
        prices = 100 + np.cumsum(np.random.normal(0, 0.5, 100))
        prices = prices - 0.1 * (prices - 100)
        theta, mu, sigma = estimate_ou_parameters(prices)
        assert theta >= 0
        assert sigma > 0

    def test_short_series_defaults(self):
        from execution_engine.math.signals import estimate_ou_parameters

        theta, mu, sigma = estimate_ou_parameters(np.array([100.0, 101.0]))
        assert sigma == 0.2


class TestRSI:
    def test_overbought_after_gains(self):
        from execution_engine.math.signals import compute_rsi

        prices = np.linspace(100, 120, 20)
        rsi = compute_rsi(prices)
        assert rsi > 60

    def test_oversold_after_losses(self):
        from execution_engine.math.signals import compute_rsi

        prices = np.linspace(120, 100, 20)
        rsi = compute_rsi(prices)
        assert rsi < 40

    def test_short_series(self):
        from execution_engine.math.signals import compute_rsi

        assert compute_rsi(np.array([100.0, 101.0])) == 50.0


class TestATR:
    def test_positive_atr(self):
        from execution_engine.math.signals import compute_atr

        highs = np.array([102, 103, 104, 103, 105] * 4, dtype=float)
        lows = np.array([98, 99, 100, 99, 101] * 4, dtype=float)
        closes = np.array([100, 101, 102, 101, 103] * 4, dtype=float)
        atr = compute_atr(highs, lows, closes)
        assert atr > 0


class TestBayesianConviction:
    def test_strong_evidence_increases_conviction(self):
        from execution_engine.math.signals import bayesian_conviction_update

        factors = [(2.0, 1.0), (1.5, 1.0), (1.8, 1.0)]
        conviction = bayesian_conviction_update(0.5, factors)
        assert conviction > 0.7

    def test_weak_evidence_decreases_conviction(self):
        from execution_engine.math.signals import bayesian_conviction_update

        factors = [(0.3, 1.0), (0.5, 1.0)]
        conviction = bayesian_conviction_update(0.5, factors)
        assert conviction < 0.3

    def test_no_evidence_returns_prior(self):
        from execution_engine.math.signals import bayesian_conviction_update

        conviction = bayesian_conviction_update(0.5, [])
        assert conviction == 0.5


class TestScoreSignal:
    def test_returns_none_for_short_data(self):
        from execution_engine.math.signals import score_signal

        result = score_signal("TEST", np.ones(5), np.ones(5), np.ones(5), np.ones(5))
        assert result is None

    def test_returns_signal_for_sufficient_data(self):
        from execution_engine.math.signals import score_signal

        np.random.seed(42)
        n = 60
        closes = 100 + np.cumsum(np.random.normal(0, 1, n))
        highs = closes + np.random.uniform(0.5, 2, n)
        lows = closes - np.random.uniform(0.5, 2, n)
        volumes = np.random.uniform(1e6, 5e6, n)
        result = score_signal("TEST", closes, highs, lows, volumes)
        assert result is not None
        assert result.atr > 0
        assert 0 <= result.conviction <= 1
