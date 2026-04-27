"""Tests for Phase 5 — multimodal PatternAgent."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from graph_intelligence.agents.pattern import (
    PatternClass,
    PatternClassification,
    chart_fingerprint,
    classify_pattern,
    pattern_analyst_opinion,
    render_candlestick_chart,
)
from graph_intelligence.agents.pattern.chart_renderer import OHLCVSeries
from graph_intelligence.graph_state.state import create_initial_state


def _synthetic_series(n: int = 60, seed: int = 42) -> OHLCVSeries:
    """Deterministic synthetic OHLCV for testing the renderer."""
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.standard_normal(n) * 0.5)
    opens = closes + rng.standard_normal(n) * 0.2
    highs = np.maximum(opens, closes) + np.abs(rng.standard_normal(n)) * 0.3
    lows = np.minimum(opens, closes) - np.abs(rng.standard_normal(n)) * 0.3
    volumes = rng.integers(1_000_000, 5_000_000, n).astype(float)
    start = dt.datetime(2026, 1, 1)
    dates = [start + dt.timedelta(days=i) for i in range(n)]
    return OHLCVSeries(dates, opens, highs, lows, closes, volumes)


@pytest.fixture(autouse=True)
def _clear_pattern_cache():
    """Pattern classifications are cached on chart fingerprint. Tests
    that mock the LLM must start with an empty cache, otherwise a
    prior test's result pollutes later tests that use the same
    synthetic series (same bytes → same fingerprint → cached hit)."""
    from graph_intelligence.agents.pattern.node import _pattern_cache

    _pattern_cache.clear()
    yield
    _pattern_cache.clear()


# ────────────────────────────────────────────────────────────────────
#  Schema
# ────────────────────────────────────────────────────────────────────
class TestSchema:
    def test_all_16_canonical_patterns_plus_none(self):
        # 16 patterns + NONE = 17 enum members
        assert len(PatternClass) == 17

    def test_classification_defaults(self):
        c = PatternClassification()
        assert c.pattern == PatternClass.NONE
        assert c.confidence == 0.0
        assert c.implied_direction == "NEUTRAL"

    def test_confidence_bounded(self):
        with pytest.raises(Exception):
            PatternClassification(confidence=1.5)

    def test_direction_literal(self):
        with pytest.raises(Exception):
            PatternClassification(implied_direction="MAYBE")


# ────────────────────────────────────────────────────────────────────
#  Chart renderer — deterministic output
# ────────────────────────────────────────────────────────────────────
class TestChartRenderer:
    def test_renders_png_bytes(self):
        series = _synthetic_series(n=60, seed=42)
        png = render_candlestick_chart(series, lookback=50)
        assert isinstance(png, bytes)
        assert len(png) > 1000
        # PNG magic bytes
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_renderer_deterministic_identical_inputs(self):
        s1 = _synthetic_series(n=60, seed=42)
        s2 = _synthetic_series(n=60, seed=42)
        png1 = render_candlestick_chart(s1, lookback=50)
        png2 = render_candlestick_chart(s2, lookback=50)
        # Same input → same fingerprint (matplotlib Agg is deterministic
        # when metadata is stripped, which our renderer does).
        assert chart_fingerprint(png1) == chart_fingerprint(png2)

    def test_renderer_differentiates_different_inputs(self):
        s1 = _synthetic_series(n=60, seed=42)
        s2 = _synthetic_series(n=60, seed=99)
        png1 = render_candlestick_chart(s1)
        png2 = render_candlestick_chart(s2)
        assert chart_fingerprint(png1) != chart_fingerprint(png2)

    def test_renderer_respects_lookback(self):
        series = _synthetic_series(n=200)
        png_short = render_candlestick_chart(series, lookback=20)
        png_long = render_candlestick_chart(series, lookback=100)
        assert chart_fingerprint(png_short) != chart_fingerprint(png_long)

    def test_empty_series_raises(self):
        empty = OHLCVSeries(
            dates=[],
            opens=np.array([]),
            highs=np.array([]),
            lows=np.array([]),
            closes=np.array([]),
            volumes=np.array([]),
        )
        with pytest.raises(ValueError):
            render_candlestick_chart(empty)

    def test_mismatched_array_lengths_raise(self):
        with pytest.raises(ValueError):
            OHLCVSeries(
                dates=[dt.datetime.now()],
                opens=np.array([1.0, 2.0]),
                highs=np.array([1.0]),
                lows=np.array([1.0]),
                closes=np.array([1.0]),
                volumes=np.array([1.0]),
            )


# ────────────────────────────────────────────────────────────────────
#  classify_pattern — mocked vision LLM
# ────────────────────────────────────────────────────────────────────
class TestClassifyPattern:
    @pytest.mark.asyncio
    async def test_returns_classification(self):
        series = _synthetic_series()
        expected = PatternClassification(
            pattern=PatternClass.DOUBLE_BOTTOM,
            confidence=0.82,
            implied_direction="LONG",
            invalidation_level=95.0,
            rationale="Two lows near 96, rebound, higher high.",
        )
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(return_value=expected)
        with patch(
            "graph_intelligence.agents.pattern.node._get_vision_llm",
            return_value=fake_llm,
        ):
            result = await classify_pattern(series, ticker="AAPL")
        assert result.pattern == PatternClass.DOUBLE_BOTTOM
        assert result.implied_direction == "LONG"

    @pytest.mark.asyncio
    async def test_snaps_contradiction_to_canonical(self):
        """If LLM labels DOUBLE_BOTTOM with SHORT direction, we normalise to LONG."""
        series = _synthetic_series()
        bad = PatternClassification(
            pattern=PatternClass.DOUBLE_BOTTOM,
            implied_direction="SHORT",  # contradicts canonical bias
            confidence=0.70,
        )
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(return_value=bad)
        with patch(
            "graph_intelligence.agents.pattern.node._get_vision_llm",
            return_value=fake_llm,
        ):
            result = await classify_pattern(series, ticker="AAPL")
        assert result.pattern == PatternClass.DOUBLE_BOTTOM
        assert result.implied_direction == "LONG"  # snapped back

    @pytest.mark.asyncio
    async def test_llm_error_returns_none(self):
        series = _synthetic_series()
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(side_effect=RuntimeError("vision api down"))
        with patch(
            "graph_intelligence.agents.pattern.node._get_vision_llm",
            return_value=fake_llm,
        ):
            result = await classify_pattern(series, ticker="AAPL")
        assert result.pattern == PatternClass.NONE
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_result_is_cached(self):
        """Second call with identical chart bytes hits the cache."""
        from graph_intelligence.agents.pattern.node import _pattern_cache

        _pattern_cache.clear()
        series = _synthetic_series()
        expected = PatternClassification(
            pattern=PatternClass.ASCENDING_TRIANGLE,
            confidence=0.70,
            implied_direction="LONG",
        )
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(return_value=expected)
        with patch(
            "graph_intelligence.agents.pattern.node._get_vision_llm",
            return_value=fake_llm,
        ):
            r1 = await classify_pattern(series, ticker="AAPL")
            r2 = await classify_pattern(series, ticker="AAPL")
        assert r1.pattern == r2.pattern
        assert fake_llm.ainvoke.await_count == 1  # second call was cached


# ────────────────────────────────────────────────────────────────────
#  pattern_analyst_opinion — analyst-swarm adapter
# ────────────────────────────────────────────────────────────────────
class TestAnalystOpinionAdapter:
    @pytest.mark.asyncio
    async def test_adapts_directional_pattern(self):
        series = _synthetic_series()
        expected = PatternClassification(
            pattern=PatternClass.HEAD_SHOULDERS,
            confidence=0.74,
            implied_direction="SHORT",
            invalidation_level=110.0,
            rationale="Three distinct peaks; middle highest.",
        )
        state = create_initial_state(shock_ticker="AAPL", target_ticker="AAPL")
        with (
            patch(
                "graph_intelligence.agents.pattern.node._fetch_ohlcv_for_pattern",
                new=AsyncMock(return_value=series),
            ),
            patch(
                "graph_intelligence.agents.pattern.node.classify_pattern",
                new=AsyncMock(return_value=expected),
            ),
        ):
            op = await pattern_analyst_opinion(state)
        assert op["analyst"] == "Pattern"
        assert op["direction"] == "SHORT"
        assert op["confidence"] == 0.74
        assert "Head" in op["thesis"] or "peaks" in op["thesis"]
        assert op["full_analysis"]["pattern"] == "HEAD_SHOULDERS"

    @pytest.mark.asyncio
    async def test_none_pattern_becomes_hold(self):
        series = _synthetic_series()
        none_result = PatternClassification(
            pattern=PatternClass.NONE,
            confidence=0.10,
        )
        state = create_initial_state(shock_ticker="AAPL", target_ticker="AAPL")
        with (
            patch(
                "graph_intelligence.agents.pattern.node._fetch_ohlcv_for_pattern",
                new=AsyncMock(return_value=series),
            ),
            patch(
                "graph_intelligence.agents.pattern.node.classify_pattern",
                new=AsyncMock(return_value=none_result),
            ),
        ):
            op = await pattern_analyst_opinion(state)
        assert op["direction"] == "HOLD"

    @pytest.mark.asyncio
    async def test_neutral_symmetric_triangle_becomes_hold(self):
        series = _synthetic_series()
        neutral = PatternClassification(
            pattern=PatternClass.SYMMETRICAL_TRIANGLE,
            confidence=0.65,
            implied_direction="NEUTRAL",
        )
        state = create_initial_state(shock_ticker="AAPL", target_ticker="AAPL")
        with (
            patch(
                "graph_intelligence.agents.pattern.node._fetch_ohlcv_for_pattern",
                new=AsyncMock(return_value=series),
            ),
            patch(
                "graph_intelligence.agents.pattern.node.classify_pattern",
                new=AsyncMock(return_value=neutral),
            ),
        ):
            op = await pattern_analyst_opinion(state)
        assert op["direction"] == "HOLD"
        assert op["confidence"] == 0.65

    @pytest.mark.asyncio
    async def test_no_ticker_returns_silent_hold(self):
        state = create_initial_state(shock_ticker="")
        state["target_ticker"] = ""
        op = await pattern_analyst_opinion(state)
        assert op["direction"] == "HOLD"
        assert op["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_no_ohlcv_returns_silent_hold(self):
        state = create_initial_state(shock_ticker="AAPL", target_ticker="AAPL")
        with patch(
            "graph_intelligence.agents.pattern.node._fetch_ohlcv_for_pattern",
            new=AsyncMock(return_value=None),
        ):
            op = await pattern_analyst_opinion(state)
        assert op["direction"] == "HOLD"
        assert op["confidence"] == 0.0


# ────────────────────────────────────────────────────────────────────
#  Debate-weight registration
# ────────────────────────────────────────────────────────────────────
class TestDebateIntegration:
    def test_pattern_analyst_has_weight(self):
        from graph_intelligence.debate import _ANALYST_WEIGHTS

        assert "Pattern" in _ANALYST_WEIGHTS
        assert _ANALYST_WEIGHTS["Pattern"] == 1.1

    def test_jsd_works_with_5_analyst_opinions(self):
        from graph_intelligence.debate import (
            _EPISTEMIC_DIVERGENCE_THRESHOLD,
            compute_epistemic_divergence,
        )

        opinions = [
            {"analyst": "Supply Chain", "direction": "LONG", "confidence": 0.80},
            {"analyst": "Fundamentals", "direction": "LONG", "confidence": 0.78},
            {"analyst": "Technicals", "direction": "LONG", "confidence": 0.82},
            {"analyst": "Macro", "direction": "LONG", "confidence": 0.75},
            {"analyst": "Pattern", "direction": "LONG", "confidence": 0.70},
        ]
        jsd = compute_epistemic_divergence(opinions)
        # 5-analyst unanimous — should be very coherent.
        assert jsd < _EPISTEMIC_DIVERGENCE_THRESHOLD
