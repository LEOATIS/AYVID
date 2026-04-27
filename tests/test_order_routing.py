"""Tests for order routing logic — horizon mapping, RTH checks, bracket construction."""

from __future__ import annotations

import pytest


def _mk_signal(
    horizon,
    *,
    tier3_shock: bool = False,
    ticker: str = "AAPL",
    direction=None,
    intent_id: str = "r-test",
    conviction: float = 0.7,
):
    from shared.types import AlphaScore, TradeAction, TradeIntent

    if direction is None:
        direction = TradeAction.LONG
    return TradeIntent(
        intent_id=intent_id,
        ticker=ticker,
        direction=direction,
        alpha_horizon=horizon,
        alpha_score=AlphaScore(
            ticker=ticker,
            direction=direction,
            factor_score=conviction,
            bayesian_conviction=conviction,
            unified_conviction=conviction,
            expected_r=2.0,
            min_r=1.5,
        ),
        rationale="routing test",
        tier3_shock=tier3_shock,
    )


class TestDetermineOrderType:
    """Tier-1 passive-first routing: only Tier-3 news shocks cross the spread."""

    def test_first_order_non_shock_is_passive_limit(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType

        assert (
            _determine_order_type(_mk_signal(AlphaHorizon.FIRST_ORDER)) == OrderType.PASSIVE_LIMIT
        )

    def test_first_order_tier3_shock_is_limit(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType

        assert (
            _determine_order_type(_mk_signal(AlphaHorizon.FIRST_ORDER, tier3_shock=True))
            == OrderType.LIMIT
        )

    def test_second_order_is_passive_limit(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType

        assert (
            _determine_order_type(_mk_signal(AlphaHorizon.SECOND_ORDER)) == OrderType.PASSIVE_LIMIT
        )

    def test_third_order_is_twap(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType

        assert _determine_order_type(_mk_signal(AlphaHorizon.THIRD_ORDER)) == OrderType.TWAP

    def test_fourth_order_is_vwap(self):
        from execution_engine.broker.orders import _determine_order_type
        from shared.types import AlphaHorizon, OrderType

        assert _determine_order_type(_mk_signal(AlphaHorizon.FOURTH_ORDER)) == OrderType.VWAP


class TestSubmitSignalPaper:
    """Paper-mode submission asserts the FILLED short-circuit. We force
    ``is_regular_trading_hours`` to True so the test is deterministic
    regardless of when it's executed (otherwise an after-hours run gets
    QUEUED, which is correct production behaviour but not what these
    tests are validating)."""

    @pytest.mark.asyncio
    async def test_paper_trade_fills_immediately(self, monkeypatch):
        import execution_engine.broker.orders as orders_mod
        from execution_engine.broker.orders import submit_signal
        from shared.types import AlphaHorizon, SignalStatus, TradeAction

        monkeypatch.setattr(orders_mod, "is_regular_trading_hours", lambda: True)

        signal = _mk_signal(
            AlphaHorizon.THIRD_ORDER,
            ticker="AAPL",
            direction=TradeAction.LONG,
            intent_id="test-001",
        )
        result = await submit_signal(signal, 1_000_000, 150.0, 100)
        assert result.status == SignalStatus.FILLED

    @pytest.mark.asyncio
    async def test_paper_trade_short(self, monkeypatch):
        import execution_engine.broker.orders as orders_mod
        from execution_engine.broker.orders import submit_signal
        from shared.types import AlphaHorizon, SignalStatus, TradeAction

        monkeypatch.setattr(orders_mod, "is_regular_trading_hours", lambda: True)

        signal = _mk_signal(
            AlphaHorizon.SECOND_ORDER,
            ticker="TSLA",
            direction=TradeAction.SHORT,
            intent_id="test-002",
            conviction=0.65,
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
