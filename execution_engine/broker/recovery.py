"""IB daily reset recovery logic.

Handles the mandatory 23:45 ET server reset:
1. Detect disconnect (Error 1100)
2. Exponential backoff reconnection
3. State reconciliation (open positions + pending orders)
4. Bracket order validation (server-side stops still active)
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

from shared.logger import get_logger
from shared.types import Position, TradeAction

log = get_logger("broker.recovery")

_ET = ZoneInfo("America/New_York")

# IB order statuses that indicate a terminal (no longer live) state.
# Source: https://interactivebrokers.github.io/tws-api/order_submission.html
_TERMINAL_STATUSES = frozenset(
    {
        "Filled",
        "Cancelled",
        "ApiCancelled",
        "PendingCancel",
        "Inactive",
    }
)

# IB order types that qualify as a protective stop. A bracket with a
# non-terminal order of any of these types covering the same symbol is
# considered "bracketed" for the purposes of orphan detection.
_STOP_ORDER_TYPES = frozenset({"STP", "STP LMT", "TRAIL", "TRAIL LIMIT"})


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
            positions.append(
                Position(
                    ticker=pos.contract.symbol,
                    side=TradeAction.LONG if pos.position > 0 else TradeAction.SHORT,
                    quantity=abs(int(pos.position)),
                    avg_entry_price=pos.avgCost,
                    current_price=pos.avgCost,
                )
            )

        log.info(f"Reconciled {len(positions)} open positions")
        return positions
    except Exception as exc:
        log.error(f"Position reconciliation failed: {exc}")
        return []


def _is_terminal_status(trade: Any) -> bool:
    """True iff the ib_async Trade is in a non-live state."""
    status = getattr(getattr(trade, "orderStatus", None), "status", "") or ""
    return status in _TERMINAL_STATUSES


async def validate_bracket_orders(ib: Any) -> dict[str, bool]:
    """Verify that every symbol with open broker state has at least one
    live STP / STP LMT / TRAIL order protecting it.

    **P0-F rewrite:** the previous implementation iterated a comprehension
    over `[trade.order]` — a one-element list containing only the parent
    order — so it was structurally blind to bracket child legs (TP/SL).
    Any bracket submitted with TP+SL children would register as "orphan"
    (false positive) while a standalone STP on another symbol would never
    credit its parent (false negative). Both failure modes could provoke
    an emergency flatten that destroys real hedging positions.

    New semantics:
      * Walk every `Trade` returned by ``reqOpenOrdersAsync``.
      * Skip trades in terminal states (Filled, Cancelled, etc.) — these
        are not protecting anything any more.
      * For each symbol seen in any non-terminal order, record whether
        at least one non-terminal STP / STP LMT / TRAIL / TRAIL LIMIT
        order exists for that symbol. Grouping is by `parentId` so
        child stops credit their parent, and standalone stops credit
        themselves.
      * Return ``{symbol: True}`` for protected symbols and
        ``{symbol: False}`` for orphans.

    Callers of ``full_recovery_sequence`` use the False entries to
    decide whether to emergency-flatten a position.
    """
    try:
        open_trades = await ib.reqOpenOrdersAsync()

        symbols_with_open_state: set[str] = set()
        stop_covered: set[str] = set()
        # Bracket map kept for operator visibility on orphaned parents.
        bracket_map: dict[int, dict[str, Any]] = defaultdict(
            lambda: {"parent_sym": None, "children": []}
        )

        for trade in open_trades:
            contract = getattr(trade, "contract", None)
            order = getattr(trade, "order", None)
            if contract is None or order is None:
                continue
            if _is_terminal_status(trade):
                continue

            symbol = getattr(contract, "symbol", None) or ""
            if not symbol:
                continue
            symbols_with_open_state.add(symbol)

            order_type = getattr(order, "orderType", "") or ""
            parent_id = int(getattr(order, "parentId", 0) or 0)

            if order_type in _STOP_ORDER_TYPES:
                stop_covered.add(symbol)

            if parent_id == 0:
                # This order is a parent (or a free-standing order)
                bracket_map[getattr(order, "orderId", 0)]["parent_sym"] = symbol
            else:
                bracket_map[parent_id]["children"].append(
                    {
                        "symbol": symbol,
                        "orderType": order_type,
                        "status": getattr(getattr(trade, "orderStatus", None), "status", "") or "",
                    }
                )

        validation: dict[str, bool] = {
            sym: (sym in stop_covered) for sym in symbols_with_open_state
        }
        orphaned = [sym for sym, ok in validation.items() if not ok]

        if orphaned:
            # Dump the bracket map so operators can see which parent
            # IDs lost their stop children (or never had them).
            log.warning(
                f"Bracket orphans (no active STP/STP LMT/TRAIL): {orphaned}. "
                f"Bracket map (parent_id -> structure): {dict(bracket_map)}"
            )
        else:
            log.info(f"Bracket validation: {len(validation)} symbols, all covered by active stops")

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
                f"Position {pos.ticker} has no active stop-loss — consider emergency flatten"
            )

    log.info("Recovery sequence complete")
    return positions
