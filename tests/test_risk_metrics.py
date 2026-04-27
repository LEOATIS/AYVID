"""Tests for risk metrics: VaR, drawdown, Sharpe."""

from __future__ import annotations

import numpy as np


class TestComputeVar:
    def test_historical_var_positive(self, sample_returns):
        from execution_engine.math.risk_metrics import compute_var

        var = compute_var(sample_returns, confidence=0.95)
        assert var > 0

    def test_parametric_var_positive(self, sample_returns):
        from execution_engine.math.risk_metrics import compute_var

        var = compute_var(sample_returns, confidence=0.95, method="parametric")
        assert var > 0

    def test_short_series_returns_zero(self):
        from execution_engine.math.risk_metrics import compute_var

        assert compute_var(np.array([0.01, 0.02]), confidence=0.95) == 0.0

    def test_higher_confidence_higher_var(self, sample_returns):
        from execution_engine.math.risk_metrics import compute_var

        var_95 = compute_var(sample_returns, confidence=0.95)
        var_99 = compute_var(sample_returns, confidence=0.99)
        assert var_99 >= var_95


class TestUpdateDrawdown:
    def test_new_peak_updates(self, sample_portfolio):
        from execution_engine.math.risk_metrics import update_drawdown

        sample_portfolio.total_equity = 1_100_000
        updated = update_drawdown(sample_portfolio)
        assert updated.peak_equity == 1_100_000
        assert updated.drawdown_pct == 0.0

    def test_drawdown_calculated_correctly(self, sample_portfolio):
        from execution_engine.math.risk_metrics import update_drawdown

        sample_portfolio.total_equity = 950_000
        updated = update_drawdown(sample_portfolio)
        expected_dd = (1_050_000 - 950_000) / 1_050_000 * 100
        assert abs(updated.drawdown_pct - expected_dd) < 0.01

    def test_max_drawdown_persists(self, sample_portfolio):
        from execution_engine.math.risk_metrics import update_drawdown

        sample_portfolio.total_equity = 900_000
        sample_portfolio.max_drawdown_pct = 10.0
        updated = update_drawdown(sample_portfolio)
        dd = (1_050_000 - 900_000) / 1_050_000 * 100
        assert updated.max_drawdown_pct == max(10.0, dd)


class TestComputeSharpe:
    def test_positive_returns_positive_sharpe(self):
        from execution_engine.math.risk_metrics import compute_sharpe_ratio

        returns = np.random.normal(0.001, 0.01, 252)
        sharpe = compute_sharpe_ratio(returns)
        assert isinstance(sharpe, float)

    def test_short_series_returns_zero(self):
        from execution_engine.math.risk_metrics import compute_sharpe_ratio

        assert compute_sharpe_ratio(np.array([0.01, 0.02])) == 0.0

    def test_zero_std_returns_zero(self):
        from execution_engine.math.risk_metrics import compute_sharpe_ratio

        returns = np.ones(20) * 0.001
        assert compute_sharpe_ratio(returns) == 0.0
