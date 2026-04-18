"""Tests for shared type models — serialization, validation, enums."""
from __future__ import annotations

import datetime as dt

import pytest


class TestTradeSignal:
    def test_create_valid_signal(self):
        from shared.types import AlphaHorizon, TradeAction, TradeSignal
        signal = TradeSignal(
            signal_id="abc123",
            ticker="AAPL",
            action=TradeAction.LONG,
            alpha_horizon=AlphaHorizon.THIRD_ORDER,
            confidence=0.75,
            rationale="Test",
        )
        assert signal.ticker == "AAPL"
        assert signal.confidence == 0.75

    def test_signal_json_roundtrip(self):
        from shared.types import AlphaHorizon, TradeAction, TradeSignal
        signal = TradeSignal(
            signal_id="abc123",
            ticker="NVDA",
            action=TradeAction.SHORT,
            alpha_horizon=AlphaHorizon.FOURTH_ORDER,
            confidence=0.60,
            rationale="Supply chain disruption",
        )
        data = signal.model_dump(mode="json")
        restored = TradeSignal.model_validate(data)
        assert restored.ticker == signal.ticker
        assert restored.action == signal.action


class TestCausalEvent:
    def test_create_event(self):
        from shared.types import CausalEvent, EventType
        event = CausalEvent(
            timestamp=dt.datetime.now(dt.timezone.utc),
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
            timestamp=dt.datetime.now(dt.timezone.utc),
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
        expected = {"SHOCK", "RECOVERY", "EARNINGS", "MACRO",
                    "GEOPOLITICAL", "REGULATORY", "COMMODITY_SHOCK", "FX_SHOCK"}
        actual = {e.value for e in EventType}
        assert expected == actual

    def test_alpha_horizons(self):
        from shared.types import AlphaHorizon
        assert len(AlphaHorizon) == 4

    def test_order_types(self):
        from shared.types import OrderType
        assert OrderType.TWAP.value == "TWAP"
        assert OrderType.VWAP.value == "VWAP"
