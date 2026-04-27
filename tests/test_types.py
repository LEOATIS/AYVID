"""Tests for shared type models — serialization, validation, enums."""

from __future__ import annotations

import datetime as dt


class TestTradeIntent:
    """Phase-3: TradeSignal was consolidated into TradeIntent.

    The `TradeSignal` alias still resolves to `TradeIntent` for in-tree
    backwards compatibility, but new call-sites must construct via the
    TradeIntent contract (including an AlphaScore).
    """

    @staticmethod
    def _intent(**overrides):
        from shared.types import AlphaHorizon, AlphaScore, TradeAction, TradeIntent

        defaults = dict(
            intent_id="abc123",
            ticker="AAPL",
            direction=TradeAction.LONG,
            alpha_horizon=AlphaHorizon.THIRD_ORDER,
            alpha_score=AlphaScore(
                ticker="AAPL",
                direction=TradeAction.LONG,
                factor_score=0.75,
                bayesian_conviction=0.75,
                unified_conviction=0.75,
                expected_r=2.0,
                min_r=1.5,
            ),
            rationale="Test",
        )
        defaults.update(overrides)
        return TradeIntent(**defaults)

    def test_create_valid_intent(self):
        intent = self._intent()
        assert intent.ticker == "AAPL"
        assert intent.alpha_score.unified_conviction == 0.75
        # Backwards-compat read-only accessors
        assert intent.confidence == 0.75
        assert intent.signal_id == "abc123"

    def test_intent_json_roundtrip(self):
        from shared.types import AlphaHorizon, TradeAction, TradeIntent

        intent = self._intent(
            intent_id="def456",
            ticker="NVDA",
            direction=TradeAction.SHORT,
            alpha_horizon=AlphaHorizon.FOURTH_ORDER,
        )
        data = intent.model_dump(mode="json")
        restored = TradeIntent.model_validate(data)
        assert restored.ticker == intent.ticker
        assert restored.direction == intent.direction
        assert restored.alpha_score.unified_conviction == 0.75

    def test_trade_signal_alias_resolves_to_trade_intent(self):
        from shared.types import TradeIntent, TradeSignal

        assert TradeSignal is TradeIntent, (
            "TradeSignal must be a direct alias for TradeIntent during Phase-3 "
            "migration; separate class would re-open the duplicate-type surface."
        )


class TestCausalEvent:
    def test_create_event(self):
        from shared.types import CausalEvent, EventType

        event = CausalEvent(
            timestamp=dt.datetime.now(dt.UTC),
            source_node="TSM",
            event_type=EventType.SHOCK,
            compressed_summary="Factory fire in Tainan",
            confidence=0.9,
            sequence_id=1,
        )
        assert event.source_node == "TSM"
        assert event.event_type == EventType.SHOCK


class TestOHLCVBar:
    def test_create_bar(self):
        from shared.types import OHLCVBar

        bar = OHLCVBar(
            timestamp=dt.datetime.now(dt.UTC),
            symbol="AAPL",
            open=150.0,
            high=155.0,
            low=149.0,
            close=153.0,
            volume=1_000_000,
        )
        assert bar.close == 153.0
        assert bar.volume == 1_000_000


class TestEnums:
    def test_all_event_types(self):
        from shared.types import EventType

        expected = {
            "SHOCK",
            "RECOVERY",
            "EARNINGS",
            "MACRO",
            "GEOPOLITICAL",
            "REGULATORY",
            "COMMODITY_SHOCK",
            "FX_SHOCK",
        }
        actual = {e.value for e in EventType}
        assert expected == actual

    def test_alpha_horizons(self):
        from shared.types import AlphaHorizon

        assert len(AlphaHorizon) == 4

    def test_order_types(self):
        from shared.types import OrderType

        assert OrderType.TWAP.value == "TWAP"
        assert OrderType.VWAP.value == "VWAP"
