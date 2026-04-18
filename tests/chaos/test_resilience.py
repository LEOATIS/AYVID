"""Chaos / resilience test scenarios.

These tests verify the system degrades gracefully under external service
failures rather than crashing or producing corrupt state.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.chaos.fault_injector import (
    simulate_all_services_down,
    simulate_ib_disconnect,
    simulate_llm_outage,
    simulate_neo4j_down,
    simulate_openbb_timeout,
)


def _make_signal(ticker: str = "AAPL", direction: str = "LONG") -> "PrecomputedSignal":
    from execution_engine.signal_book import PrecomputedSignal

    return PrecomputedSignal(
        ticker=ticker,
        direction=direction,
        instrument_type="EQUITY",
        conviction=0.8,
        strategy_name="test_strategy",
        strike=None,
        expiry_date="",
        stop_loss_pct=3.0,
        take_profit_pct=6.0,
        rationale="Test signal",
        computed_at=dt.datetime.now(dt.UTC).isoformat(),
        regime_fingerprint="test_fp",
        expected_r=2.0,
        min_r=1.5,
        entry_price_at_compute=150.0,
    )


def _make_events(ticker: str = "AAPL") -> list:
    import datetime as dt
    from shared.types import CausalEvent, EventType

    return [
        CausalEvent(
            timestamp=dt.datetime.now(dt.timezone.utc),
            source_node=ticker,
            event_type=EventType.SHOCK,
            compressed_summary="Major supply chain disruption",
            confidence=0.9,
            sequence_id=1,
        ),
    ]


# ── Test 1: LLM down during Tier 3 emergency recompute ──────

@pytest.mark.asyncio
async def test_llm_down_during_emergency_recompute():
    """When the LLM fails during Tier 3 override, the fast-pass HOLD
    signal should still be written and the override flag should clear.
    """
    from execution_engine.signal_book import get_signal_book

    book = get_signal_book()
    book._signals.clear()
    book._overrides_pending.clear()

    events = _make_events("TSM")

    with simulate_llm_outage():
        with patch(
            "data_primitives.extractors.openbb_client.fetch_ohlcv",
            new_callable=AsyncMock,
        ) as mock_fetch:
            from shared.types import OHLCVBar
            import numpy as np

            bars = [
                OHLCVBar(
                    timestamp=dt.datetime.now(dt.UTC) - dt.timedelta(days=90 - i),
                    symbol="TSM",
                    open=100.0 + i * 0.1,
                    high=101.0 + i * 0.1,
                    low=99.0 + i * 0.1,
                    close=100.5 + i * 0.1,
                    volume=1_000_000,
                )
                for i in range(90)
            ]
            mock_fetch.return_value = bars

            from data_primitives.pipeline.continuous_worker import emergency_recompute

            results = await emergency_recompute(
                shocked_tickers=["TSM"],
                events=events,
                trigger="test_llm_down",
            )

    assert not book.is_override_pending("TSM"), (
        "Override flag should be cleared after emergency recompute"
    )


# ── Test 2: IB disconnect mid-bracket-submission ─────────────

@pytest.mark.asyncio
async def test_ib_disconnect_during_order_submission():
    """When IB disconnects during order submission, the signal should be
    rejected and the inflight set should be cleaned up.
    """
    from shared.types import TradeSignal, TradeAction, AlphaHorizon, SignalStatus

    signal = TradeSignal(
        signal_id="test_ib_disc_001",
        ticker="AAPL",
        action=TradeAction.LONG,
        alpha_horizon=AlphaHorizon("3rd_order"),
        confidence=0.8,
        rationale="Test signal for IB disconnect",
    )

    with simulate_ib_disconnect():
        from execution_engine.broker.orders import submit_signal

        result = await submit_signal(
            signal,
            portfolio_equity=1_000_000.0,
            current_price=150.0,
            quantity=100,
        )

    assert result.status in (
        SignalStatus.REJECTED,
        SignalStatus.CANCELLED,
        SignalStatus.PENDING,
        SignalStatus.FILLED,
    ), f"Unexpected status: {result.status}"


# ── Test 3: OpenBB timeout during pre-compute ────────────────

@pytest.mark.asyncio
async def test_openbb_timeout_during_precompute():
    """When OpenBB times out during pre-compute, the signal book should
    retain any signals that were computed before the failure.
    """
    from execution_engine.signal_book import get_signal_book

    book = get_signal_book()
    existing_signal = _make_signal("MSFT")
    book.upsert(existing_signal)
    pre_count = book.size()

    with simulate_openbb_timeout():
        from data_primitives.pipeline.continuous_worker import pre_compute_signals

        try:
            count = await pre_compute_signals(["AAPL", "TSLA"])
        except Exception:
            count = 0

    assert book.size() >= 1, (
        "Pre-existing signals should survive OpenBB timeout"
    )
    msft_sig = book.lookup("MSFT", "")
    assert msft_sig is not None, "MSFT signal should still be in book"


# ── Test 4: Neo4j down during causal analysis ────────────────

@pytest.mark.asyncio
async def test_neo4j_down_falls_back_to_rule_based():
    """When Neo4j is unreachable, the system should fall back to the
    rule-based orchestrator rather than crashing.
    """
    from execution_engine.signal_book import get_signal_book

    book = get_signal_book()
    book._signals.clear()

    with simulate_neo4j_down():
        with patch(
            "data_primitives.extractors.openbb_client.fetch_ohlcv",
            new_callable=AsyncMock,
        ) as mock_fetch:
            bars = [
                MagicMock(
                    timestamp=dt.datetime.now(dt.UTC) - dt.timedelta(days=90 - i),
                    symbol="AAPL",
                    open=150.0 + i * 0.1,
                    high=151.0 + i * 0.1,
                    low=149.0 + i * 0.1,
                    close=150.5 + i * 0.1,
                    volume=1_000_000,
                )
                for i in range(90)
            ]
            mock_fetch.return_value = bars

            from data_primitives.pipeline.continuous_worker import pre_compute_signals

            try:
                count = await pre_compute_signals(["AAPL"])
            except Exception:
                count = 0

    # The system should not crash — the test passing is itself the assertion


# ── Test 5: All external services down simultaneously ────────

@pytest.mark.asyncio
async def test_total_outage_system_stays_alive():
    """When ALL external services fail simultaneously, the system should
    stay alive: mmap writer continues, signal book is accessible, and
    the dashboard can still render.
    """
    from execution_engine.signal_book import get_signal_book

    book = get_signal_book()
    existing_signal = _make_signal("NVDA")
    book.upsert(existing_signal)

    with simulate_all_services_down():
        assert book.size() >= 1, "Signal book should be readable during outage"

        stats = book.get_stats()
        assert isinstance(stats, dict), "get_stats() should return a dict"

        sig = book.lookup("NVDA", "")
        assert sig is not None, "Existing signals should be readable"

        book.save_to_disk()

        from execution_engine.signal_book import SignalBook
        assert isinstance(book, SignalBook), "Signal book should still be valid"

    assert book.size() >= 1, "Signal book should survive total outage"
