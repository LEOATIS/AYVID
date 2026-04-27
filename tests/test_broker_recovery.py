"""Tests for broker recovery + reconnect idempotency.

Covers the P0-F (bracket orphan detection) and P0-G (reconnect-storm
single-flight guard) contracts. These are the invariants that keep
AYVID from either (a) emergency-flattening a correctly-bracketed
position because the audit pass was structurally blind to TP/SL legs,
or (b) running N parallel ``full_recovery_sequence`` coroutines that
race on orphan discovery after a single connection flap.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# ── Fake ib_async Trade / Order plumbing ─────────────────────────────


def _make_trade(
    *,
    symbol: str,
    order_id: int,
    parent_id: int = 0,
    order_type: str = "LMT",
    status: str = "Submitted",
) -> SimpleNamespace:
    """Build a minimal stand-in for an ib_async Trade. Real Trade has
    .contract, .order, .orderStatus — that's all `validate_bracket_orders`
    reads.
    """
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=symbol),
        order=SimpleNamespace(
            orderId=order_id,
            parentId=parent_id,
            orderType=order_type,
        ),
        orderStatus=SimpleNamespace(status=status),
    )


def _fake_ib(open_trades: list[SimpleNamespace]) -> SimpleNamespace:
    """Build a fake `ib` object whose `reqOpenOrdersAsync()` returns the
    provided list. That's the only method `validate_bracket_orders` touches.
    """
    return SimpleNamespace(
        reqOpenOrdersAsync=AsyncMock(return_value=open_trades),
    )


# ── P0-F: bracket orphan detection ───────────────────────────────────


class TestValidateBracketOrders:
    @pytest.mark.asyncio
    async def test_complete_bracket_is_credited_with_stop(self):
        """Parent LMT + TP LMT child + STP child on the same symbol —
        the previous implementation iterated over ``[trade.order]`` and
        saw only the parent's orderType (LMT), so it reported 'no stop'
        for AAPL. The rewrite groups by symbol and credits the STP child.
        """
        from execution_engine.broker.recovery import validate_bracket_orders

        parent = _make_trade(symbol="AAPL", order_id=101, order_type="LMT")
        take_profit = _make_trade(
            symbol="AAPL",
            order_id=102,
            parent_id=101,
            order_type="LMT",
        )
        stop_loss = _make_trade(
            symbol="AAPL",
            order_id=103,
            parent_id=101,
            order_type="STP",
        )
        ib = _fake_ib([parent, take_profit, stop_loss])

        result = await validate_bracket_orders(ib)

        assert result == {"AAPL": True}, (
            "bracketed symbol with active STP child must be credited — P0-F regression"
        )

    @pytest.mark.asyncio
    async def test_orphan_parent_without_stop_child_is_flagged(self):
        """Parent + TP child but NO stop child — the symbol must be
        flagged as orphaned so `full_recovery_sequence` can surface an
        emergency-flatten warning."""
        from execution_engine.broker.recovery import validate_bracket_orders

        parent = _make_trade(symbol="TSLA", order_id=201, order_type="LMT")
        take_profit = _make_trade(
            symbol="TSLA",
            order_id=202,
            parent_id=201,
            order_type="LMT",
        )
        ib = _fake_ib([parent, take_profit])

        result = await validate_bracket_orders(ib)

        assert result == {"TSLA": False}

    @pytest.mark.asyncio
    async def test_standalone_stop_credits_its_symbol(self):
        """A free-standing STP (parentId=0) on MSFT credits its symbol
        just as a bracketed child would. No false 'orphan' should be
        reported merely because the stop is standalone."""
        from execution_engine.broker.recovery import validate_bracket_orders

        standalone_stop = _make_trade(
            symbol="MSFT",
            order_id=301,
            order_type="STP",
        )
        ib = _fake_ib([standalone_stop])

        result = await validate_bracket_orders(ib)

        assert result == {"MSFT": True}

    @pytest.mark.asyncio
    async def test_terminal_stop_does_not_count_as_protection(self):
        """A Cancelled stop is not active — its symbol must be orphan."""
        from execution_engine.broker.recovery import validate_bracket_orders

        parent = _make_trade(symbol="NVDA", order_id=401, order_type="LMT")
        dead_stop = _make_trade(
            symbol="NVDA",
            order_id=402,
            parent_id=401,
            order_type="STP",
            status="Cancelled",
        )
        ib = _fake_ib([parent, dead_stop])

        result = await validate_bracket_orders(ib)

        assert result == {"NVDA": False}

    @pytest.mark.asyncio
    async def test_filled_parent_with_active_stop_still_protected(self):
        """Common mid-flight state: parent filled, TP live, SL live.
        The symbol is still bracketed — the active STP protects the
        realised position. Only the live trades should be considered."""
        from execution_engine.broker.recovery import validate_bracket_orders

        filled_parent = _make_trade(
            symbol="GOOG",
            order_id=501,
            order_type="LMT",
            status="Filled",
        )
        live_tp = _make_trade(
            symbol="GOOG",
            order_id=502,
            parent_id=501,
            order_type="LMT",
        )
        live_stop = _make_trade(
            symbol="GOOG",
            order_id=503,
            parent_id=501,
            order_type="STP",
        )
        ib = _fake_ib([filled_parent, live_tp, live_stop])

        result = await validate_bracket_orders(ib)

        assert result == {"GOOG": True}, (
            "symbol with active STP child must remain protected even if parent has already filled"
        )

    @pytest.mark.asyncio
    async def test_trail_stop_counts_as_protection(self):
        """Trailing stops (TRAIL, TRAIL LIMIT) qualify as protective
        stops. Verifies the stop-type list is inclusive."""
        from execution_engine.broker.recovery import validate_bracket_orders

        parent = _make_trade(symbol="AMZN", order_id=601, order_type="LMT")
        trail = _make_trade(
            symbol="AMZN",
            order_id=602,
            parent_id=601,
            order_type="TRAIL",
        )
        ib = _fake_ib([parent, trail])

        result = await validate_bracket_orders(ib)

        assert result == {"AMZN": True}

    @pytest.mark.asyncio
    async def test_multi_symbol_mixed_coverage(self):
        """Several symbols at once, some bracketed, some orphaned.
        Exercises the grouping logic end-to-end."""
        from execution_engine.broker.recovery import validate_bracket_orders

        trades = [
            # SPY: bracketed
            _make_trade(symbol="SPY", order_id=1, order_type="LMT"),
            _make_trade(symbol="SPY", order_id=2, parent_id=1, order_type="STP"),
            # QQQ: orphan (no stop)
            _make_trade(symbol="QQQ", order_id=3, order_type="LMT"),
            _make_trade(symbol="QQQ", order_id=4, parent_id=3, order_type="LMT"),
            # TLT: standalone STP only
            _make_trade(symbol="TLT", order_id=5, order_type="STP"),
        ]
        ib = _fake_ib(trades)

        result = await validate_bracket_orders(ib)

        assert result == {"SPY": True, "QQQ": False, "TLT": True}

    @pytest.mark.asyncio
    async def test_ib_failure_returns_empty_dict(self):
        """reqOpenOrdersAsync raising must be caught — recovery paths
        should continue with other checks rather than crashing."""
        from execution_engine.broker.recovery import validate_bracket_orders

        ib = SimpleNamespace(
            reqOpenOrdersAsync=AsyncMock(side_effect=RuntimeError("no connection")),
        )

        result = await validate_bracket_orders(ib)

        assert result == {}


# ── P0-G: single-flight reconnect guard ─────────────────────────────


class TestReconnectSingleFlight:
    @pytest.mark.asyncio
    async def test_disconnect_schedules_exactly_one_recovery(self, monkeypatch):
        """The audit P0-G defect: a flapping IB connection scheduled N
        parallel `reconnect_with_backoff` tasks, each running its own
        `full_recovery_sequence`. The guard must ensure exactly one
        recovery is in flight at a time regardless of how many
        disconnect events fire.
        """
        import execution_engine.broker.connection as conn

        # Wait-for-release sentinel so we can drive the task scheduler
        # deterministically.
        release = asyncio.Event()
        call_count = 0

        async def fake_reconnect():
            nonlocal call_count
            call_count += 1
            await release.wait()
            return "ok"

        monkeypatch.setattr(conn, "reconnect_with_backoff", fake_reconnect)

        # Reset guard state before the test
        conn._recovery_task = None

        # Fire N rapid disconnect events — the flap simulation
        for _ in range(5):
            conn._on_disconnect()

        # One recovery task should be in flight, not five
        assert conn._recovery_task is not None
        assert not conn._recovery_task.done()

        # Let the fake recovery coroutine resume and complete
        release.set()
        await conn._recovery_task

        assert call_count == 1, (
            f"flap triggered {call_count} parallel reconnects — P0-G regression"
        )
        # Done-callback must have cleared the reference
        assert conn._recovery_task is None

    @pytest.mark.asyncio
    async def test_new_disconnect_after_completion_schedules_fresh_recovery(self, monkeypatch):
        """After a recovery task completes and the done-callback fires,
        a subsequent disconnect must schedule a new recovery — the guard
        only blocks while one is live, not forever.
        """
        import execution_engine.broker.connection as conn

        call_count = 0

        async def fake_reconnect():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0)
            return "ok"

        monkeypatch.setattr(conn, "reconnect_with_backoff", fake_reconnect)
        conn._recovery_task = None

        conn._on_disconnect()
        assert conn._recovery_task is not None
        await conn._recovery_task
        # done-callback runs on the same loop cycle; yield once
        await asyncio.sleep(0)
        assert conn._recovery_task is None

        conn._on_disconnect()
        assert conn._recovery_task is not None
        await conn._recovery_task
        await asyncio.sleep(0)

        assert call_count == 2, "second disconnect failed to trigger a fresh recovery"

    @pytest.mark.asyncio
    async def test_done_callback_clears_task_on_failure_too(self, monkeypatch):
        """Recovery raising must not leave the guard stuck — the next
        disconnect must still be able to schedule a retry."""
        import execution_engine.broker.connection as conn

        call_count = 0

        async def failing_reconnect():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated reconnect failure")

        monkeypatch.setattr(conn, "reconnect_with_backoff", failing_reconnect)
        conn._recovery_task = None

        conn._on_disconnect()
        task = conn._recovery_task
        assert task is not None
        try:
            await task
        except RuntimeError:
            pass
        # Yield for done-callback to run
        await asyncio.sleep(0)

        assert conn._recovery_task is None, (
            "done-callback did not clear the guard on failure — "
            "operator will be stuck until restart"
        )

        conn._on_disconnect()
        assert conn._recovery_task is not None
        try:
            await conn._recovery_task
        except RuntimeError:
            pass
        await asyncio.sleep(0)

        assert call_count == 2
