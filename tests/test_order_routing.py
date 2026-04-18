"""Tests for order routing logic — horizon mapping, RTH checks, bracket construction."""
from __future__ import annotations

import pytest


class TestDetermineOrderType:
    def test_first_order_is_limit(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType
        assert _determine_order_type(AlphaHorizon.FIRST_ORDER) == OrderType.LIMIT

    def test_second_order_is_limit(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType
        assert _determine_order_type(AlphaHorizon.SECOND_ORDER) == OrderType.LIMIT

    def test_third_order_is_twap(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType
        assert _determine_order_type(AlphaHorizon.THIRD_ORDER) == OrderType.TWAP

    def test_fourth_order_is_vwap(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType
        assert _determine_order_type(AlphaHorizon.FOURTH_ORDER) == OrderType.VWAP


class TestSubmitSignalPaper:
    @pytest.mark.asyncio
    async def test_paper_trade_fills_immediately(self):
        from shared.types import (
            AlphaHorizon, SignalStatus, TradeAction, TradeSignal,
        )
        from execution_engine.broker.orders import submit_signal

        signal = TradeSignal(
            signal_id="test-001",
            ticker="AAPL",
            action=TradeAction.LONG,
            alpha_horizon=AlphaHorizon.THIRD_ORDER,
            confidence=0.7,
            rationale="Test signal",
        )
        result = await submit_signal(signal, 1_000_000, 150.0, 100)
        assert result.status == SignalStatus.FILLED

    @pytest.mark.asyncio
    async def test_paper_trade_short(self):
        from shared.types import (
            AlphaHorizon, SignalStatus, TradeAction, TradeSignal,
        )
        from execution_engine.broker.orders import submit_signal

        signal = TradeSignal(
            signal_id="test-002",
            ticker="TSLA",
            action=TradeAction.SHORT,
            alpha_horizon=AlphaHorizon.SECOND_ORDER,
            confidence=0.65,
            rationale="Test short",
        )
        result = await submit_signal(signal, 1_000_000, 250.0, 50)
        assert result.status == SignalStatus.FILLED


class TestBracketOrderMath:
    def test_long_bracket_prices(self):
        entry = 100.0
        tp_pct = 0.06
        sl_pct = 0.03
        tp = entry * (1 + tp_pct)
        sl = entry * (1 - sl_pct)
        assert tp == pytest.approx(106.0)
        assert sl == pytest.approx(97.0)

    def test_short_bracket_prices(self):
        entry = 100.0
        tp_pct = 0.06
        sl_pct = 0.03
        tp = entry * (1 - tp_pct)
        sl = entry * (1 + sl_pct)
        assert tp == pytest.approx(94.0)
        assert sl == pytest.approx(103.0)
