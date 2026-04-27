"""Tests for market regime detection."""

from __future__ import annotations

import numpy as np


class TestVolatilityClassification:
    def test_low_vol_regime(self):
        from execution_engine.math.regime import classify_volatility

        np.random.seed(42)
        returns = np.random.normal(0.0001, 0.005, 200)
        regime, vol, pct = classify_volatility(returns)
        assert vol < 0.3

    def test_returns_valid_regime(self):
        from execution_engine.math.regime import VolatilityRegime, classify_volatility

        returns = np.random.normal(0, 0.02, 100)
        regime, vol, pct = classify_volatility(returns)
        assert isinstance(regime, VolatilityRegime)
        assert 0 <= pct <= 100


class TestADX:
    def test_trending_market_high_adx(self):
        from execution_engine.math.regime import compute_adx

        n = 50
        closes = np.linspace(100, 130, n)
        highs = closes + 1
        lows = closes - 1
        adx = compute_adx(highs, lows, closes)
        assert adx > 15

    def test_short_series(self):
        from execution_engine.math.regime import compute_adx

        adx = compute_adx(np.ones(5), np.ones(5), np.ones(5))
        assert adx == 20.0


class TestRegimeClassification:
    def test_returns_regime_state(self):
        from execution_engine.math.regime import classify_regime

        np.random.seed(42)
        n = 80
        closes = 100 + np.cumsum(np.random.normal(0, 1, n))
        highs = closes + np.random.uniform(0.5, 2, n)
        lows = closes - np.random.uniform(0.5, 2, n)
        volumes = np.random.uniform(1e6, 5e6, n)
        state = classify_regime(closes, highs, lows, volumes)
        assert hasattr(state, "tradeable")
        assert hasattr(state, "risk_scaling_factor")
        assert 0 <= state.risk_scaling_factor <= 1.0


class TestDebateProtocol:
    def test_unanimous_long(self):
        from graph_intelligence.debate import run_debate

        opinions = [
            {
                "analyst": "Fundamentals",
                "direction": "LONG",
                "confidence": 0.8,
                "thesis": "Strong fundamentals",
            },
            {
                "analyst": "Technicals",
                "direction": "LONG",
                "confidence": 0.75,
                "thesis": "Oversold bounce",
            },
            {
                "analyst": "Macro",
                "direction": "LONG",
                "confidence": 0.7,
                "thesis": "Risk-on regime",
            },
            {
                "analyst": "Supply Chain",
                "direction": "LONG",
                "confidence": 0.85,
                "thesis": "Supply chain recovery",
            },
        ]
        result = run_debate(opinions)
        assert result.consensus_direction == "LONG"
        assert result.should_trade is True
        assert result.bayesian_conviction > 0.6

    def test_split_vote_no_trade(self):
        from graph_intelligence.debate import run_debate

        opinions = [
            {"analyst": "Fundamentals", "direction": "LONG", "confidence": 0.6, "thesis": "Buy"},
            {"analyst": "Technicals", "direction": "SHORT", "confidence": 0.7, "thesis": "Sell"},
            {"analyst": "Macro", "direction": "HOLD", "confidence": 0.5, "thesis": "Wait"},
            {"analyst": "Supply Chain", "direction": "SHORT", "confidence": 0.6, "thesis": "Risk"},
        ]
        result = run_debate(opinions)
        assert result.should_trade is False

    def test_empty_opinions(self):
        from graph_intelligence.debate import run_debate

        result = run_debate([])
        assert result.should_trade is False
        assert result.consensus_direction == "HOLD"
