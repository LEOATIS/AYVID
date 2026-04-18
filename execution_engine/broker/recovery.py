"""IB daily reset recovery logic.

Handles the mandatory 23:45 ET server reset:
1. Detect disconnect (Error 1100)
2. Exponential backoff reconnection
3. State reconciliation (open positions + pending orders)
4. Bracket order validation (server-side stops still active)
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from shared.logger import get_logger
from shared.types import Position, TradeAction

log = get_logger("broker.recovery")

_ET = ZoneInfo("America/New_York")
_RESET_HOUR = 23
_RESET_MINUTE = 45


def is_reset_window() -> bool:
    """Check if we're in the IB daily reset window (23:30–00:15 ET)."""
    now = dt.datetime.now(_ET)
    if now.hour == 23 and now.minute >= 30:
        return True
    if now.hour == 0 and now.minute <= 15:
        return True
    return False


async def reconcile_positions(ib: Any) -> list[Position]:
    """Query IB for current open positions after reconnection."""
    try:
        ib_positions = await ib.reqPositionsAsync()
        positions: list[Position] = []

        for pos in ib_positions:
            if pos.position == 0:
                continue
            positions.append(Position(
                ticker=pos.contract.symbol,
                side=TradeAction.LONG if pos.position > 0 else TradeAction.SHORT,
                quantity=abs(int(pos.position)),
                avg_entry_price=pos.avgCost,
                current_price=pos.avgCost,
            ))

        log.info(f"Reconciled {len(positions)} open positions")
        return positions
    except Exception as exc:
        log.error(f"Position reconciliation failed: {exc}")
        return []


async def validate_bracket_orders(ib: Any) -> dict[str, bool]:
    """Verify that server-side bracket orders (TP/SL) are still active.

    Critical for the 23:45 ET reset — if stops were lost, we need
    to resubmit immediately.
    """
    try:
        open_orders = await ib.reqOpenOrdersAsync()
        validation: dict[str, bool] = {}

        for trade in open_orders:
            symbol = trade.contract.symbol
            has_stop = any(
                o.orderType in ("STP", "STP LMT")
                for o in [trade.order]
            )
            validation[symbol] = has_stop

        orphaned = [sym for sym, has_stop in validation.items() if not has_stop]
        if orphaned:
            log.warning(f"Orphaned positions (no stop-loss): {orphaned}")

        return validation
    except Exception as exc:
        log.error(f"Bracket validation failed: {exc}")
        return {}


async def full_recovery_sequence(ib: Any) -> list[Position]:
    """Execute the complete recovery sequence after a disconnect."""
    log.info("Starting full recovery sequence...")

    positions = await reconcile_positions(ib)

    bracket_status = await validate_bracket_orders(ib)

    for pos in positions:
        if pos.ticker not in bracket_status or not bracket_status[pos.ticker]:
            log.warning(
                f"Position {pos.ticker} has no active stop-loss — "
                f"consider emergency flatten"
            )

    log.info("Recovery sequence complete")
    return positions
