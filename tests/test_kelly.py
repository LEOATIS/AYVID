"""Tests for Kelly Criterion position sizing math."""

from __future__ import annotations

import numpy as np


class TestKellyFractionSingle:
    def test_positive_edge_returns_positive_fraction(self):
        from execution_engine.math.kelly import kelly_fraction_single

        frac = kelly_fraction_single(win_probability=0.6, win_loss_ratio=1.5)
        assert frac > 0

    def test_no_edge_returns_zero(self):
        from execution_engine.math.kelly import kelly_fraction_single

        frac = kelly_fraction_single(win_probability=0.4, win_loss_ratio=0.5)
        assert frac == 0.0

    def test_negative_ratio_returns_zero(self):
        from execution_engine.math.kelly import kelly_fraction_single

        frac = kelly_fraction_single(win_probability=0.6, win_loss_ratio=-1.0)
        assert frac == 0.0

    def test_fraction_clamped_to_max_position(self):
        from execution_engine.math.kelly import kelly_fraction_single
        from shared.config import settings

        frac = kelly_fraction_single(win_probability=0.99, win_loss_ratio=100.0)
        assert frac <= settings.MAX_POSITION_PCT / 100.0

    def test_half_kelly_applied(self):
        from execution_engine.math.kelly import kelly_fraction_single
        from shared.config import settings

        frac = kelly_fraction_single(win_probability=0.7, win_loss_ratio=2.0)
        raw_kelly = 0.7 - 0.3 / 2.0
        assert frac <= raw_kelly * settings.KELLY_FRACTION + 1e-9

    def test_coin_flip_zero_edge(self):
        from execution_engine.math.kelly import kelly_fraction_single

        frac = kelly_fraction_single(win_probability=0.5, win_loss_ratio=1.0)
        assert frac == 0.0


class TestMultivariateKelly:
    def test_empty_input_returns_empty(self):
        from execution_engine.math.kelly import multivariate_kelly

        result = multivariate_kelly(np.array([]), np.array([[]]))
        assert len(result) == 0

    def test_single_asset_positive_return(self):
        from execution_engine.math.kelly import multivariate_kelly

        mu = np.array([0.15])
        cov = np.array([[0.04]])
        alloc = multivariate_kelly(mu, cov)
        assert len(alloc) == 1
        assert alloc[0] > 0

    def test_two_assets_allocation_sums_to_at_most_one(self):
        from execution_engine.math.kelly import multivariate_kelly

        mu = np.array([0.10, 0.12])
        cov = np.array([[0.04, 0.01], [0.01, 0.05]])
        alloc = multivariate_kelly(mu, cov)
        assert np.sum(np.abs(alloc)) <= 1.0 + 1e-9

    def test_singular_matrix_uses_pseudoinverse(self):
        from execution_engine.math.kelly import multivariate_kelly

        mu = np.array([0.10, 0.10])
        cov = np.array([[0.04, 0.04], [0.04, 0.04]])
        alloc = multivariate_kelly(mu, cov)
        assert len(alloc) == 2


class TestComputePositionSize:
    def test_basic_sizing(self):
        from execution_engine.math.kelly import compute_position_size

        shares = compute_position_size(
            portfolio_equity=1_000_000,
            allocation_fraction=0.05,
            current_price=100.0,
        )
        assert shares == 500

    def test_zero_price_returns_zero(self):
        from execution_engine.math.kelly import compute_position_size

        assert compute_position_size(1_000_000, 0.05, 0.0) == 0

    def test_zero_fraction_returns_zero(self):
        from execution_engine.math.kelly import compute_position_size

        assert compute_position_size(1_000_000, 0.0, 100.0) == 0


class TestOptimalAllocationForSignal:
    def test_returns_tuple(self):
        from execution_engine.math.kelly import optimal_allocation_for_signal

        frac, shares = optimal_allocation_for_signal(
            portfolio_equity=1_000_000,
            win_probability=0.65,
            expected_return=0.08,
            volatility=0.20,
            current_price=150.0,
        )
        assert isinstance(frac, float)
        assert isinstance(shares, int)
        assert frac >= 0
        assert shares >= 0

    def test_zero_volatility_defaults(self):
        from execution_engine.math.kelly import optimal_allocation_for_signal

        frac, shares = optimal_allocation_for_signal(
            portfolio_equity=1_000_000,
            win_probability=0.65,
            expected_return=0.08,
            volatility=0.0,
            current_price=150.0,
        )
        assert frac >= 0
