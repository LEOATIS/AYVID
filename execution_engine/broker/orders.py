"""Order routing engine — multi-horizon execution logic.

Routing policy (Tier-1 institutional microstructure):

    Tier-3 news shock               → LIMIT / cross-spread (take liquidity)
    1st-order (non-shock)            → PASSIVE_LIMIT (post-only, pegged NBB)
    2nd-order causal                 → PASSIVE_LIMIT
    3rd-order structural             → TWAP
    4th-order slow-macro             → VWAP

PASSIVE_LIMIT = IBKR Relative (REL) order with:
  - Price pegged to near-touch (NBB for buys, NBO for sells)
  - Adaptive re-peg as the quote moves
  - Cancels after `PASSIVE_PEG_TIMEOUT_SEC` (default 30s) if unfilled
  - NEVER crosses the spread. If the strategy demands immediate
    liquidity (tier3_shock=True) the signal is re-submitted as
    LIMIT-at-market instead.

Rationale: every tick of spread captured = ~15 bps of alpha preserved.
Over 250 trades/quarter at ~1 bps/trade edge loss, that is >4% of
annual return currently being handed to market-makers.

Implements:
- Bracket orders (entry + take-profit + stop-loss)
- Pre-market trap mitigation
- Regular Trading Hours enforcement
- Passive peg-and-trail with auto-cancel
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from shared.config import settings
from shared.exceptions import IBKROrderRejectedError, PreMarketTrapError
from shared.logger import get_logger
from shared.types import (
    AlphaHorizon,
    BracketOrder,
    OrderType,
    SignalStatus,
    TradeAction,
    TradeSignal,
)

log = get_logger("broker.orders")

_ET = ZoneInfo("America/New_York")
_RTH_OPEN = dt.time(9, 30)
_RTH_CLOSE = dt.time(16, 0)
_SAFE_OPEN = dt.time(9, 45)

# Passive-routing knobs (Tier-1 microstructure).
PASSIVE_PEG_TIMEOUT_SEC = 30           # cancel if unfilled after this window
PASSIVE_PEG_POLL_INTERVAL_SEC = 2      # how often to re-check fill / re-peg
PASSIVE_REPEG_TICK_THRESHOLD = 2       # re-peg when quote drifts >N ticks

_order_queue: list[TradeSignal] = []
_inflight_signals: set[str] = set()


def is_regular_trading_hours() -> bool:
    """Check if we're within Regular Trading Hours (09:30–16:00 ET)."""
    now_et = dt.datetime.now(_ET).time()
    return _RTH_OPEN <= now_et <= _RTH_CLOSE


def _determine_order_type(signal: TradeSignal) -> OrderType:
    """Route order type based on signal metadata.

    The single institutional rule: we take liquidity ONLY for explicit
    Tier-3 news shocks. Everything else routes passive to capture the
    maker side of the spread.
    """
    if getattr(signal, "tier3_shock", False):
        return OrderType.LIMIT  # cross the spread — news event needs fill now

    match signal.alpha_horizon:
        case AlphaHorizon.FIRST_ORDER:
            return OrderType.PASSIVE_LIMIT  # patient entry on structural alpha
        case AlphaHorizon.SECOND_ORDER:
            return OrderType.PASSIVE_LIMIT
        case AlphaHorizon.THIRD_ORDER:
            return OrderType.TWAP
        case AlphaHorizon.FOURTH_ORDER:
            return OrderType.VWAP
        case _:
            return OrderType.PASSIVE_LIMIT


async def submit_signal(
    signal: TradeSignal,
    portfolio_equity: float,
    current_price: float,
    quantity: int,
) -> TradeSignal:
    """Process a trade signal through the pre-market and order routing logic."""

    if not is_regular_trading_hours():
        now_et = dt.datetime.now(_ET)
        log.warning(
            f"Signal for {signal.ticker} generated outside RTH "
            f"({now_et.strftime('%H:%M')} ET) — queueing"
        )
        signal.status = SignalStatus.QUEUED
        _order_queue.append(signal)
        return signal

    order_type = _determine_order_type(signal)
    ib_action = "SELL" if signal.action == TradeAction.SHORT else "BUY"

    stop_loss_pct = 0.03
    take_profit_pct = 0.06 if signal.alpha_horizon in (
        AlphaHorizon.THIRD_ORDER, AlphaHorizon.FOURTH_ORDER
    ) else 0.03

    if ib_action == "BUY":
        take_profit = current_price * (1 + take_profit_pct)
        stop_loss = current_price * (1 - stop_loss_pct)
    else:
        take_profit = current_price * (1 - take_profit_pct)
        stop_loss = current_price * (1 + stop_loss_pct)

    bracket = BracketOrder(
        ticker=signal.ticker,
        action=ib_action,
        quantity=quantity,
        order_type=order_type,
        limit_price=current_price,
        take_profit_price=round(take_profit, 2),
        stop_loss_price=round(stop_loss, 2),
    )

    if settings.PAPER_TRADING:
        signal.status = SignalStatus.FILLED
        log.info(
            f"[PAPER] {ib_action} {quantity} {signal.ticker} @ ${current_price:.2f} | "
            f"TP=${bracket.take_profit_price:.2f} SL=${bracket.stop_loss_price:.2f} | "
            f"Type={order_type.value}"
        )
        return signal

    signal = await _submit_to_ib(signal, bracket)
    return signal


def _build_ib_contract(
    ticker: str,
    asset_class: str = "EQUITY",
    exchange: str = "SMART",
    expiry: str = "",
    strike: float = 0.0,
    right: str = "",
    multiplier: str = "",
):
    """Build the appropriate ib_async contract for any asset class."""
    from ib_async import Stock, Future, Option

    match asset_class.upper():
        case "FUTURE":
            return Future(
                symbol=ticker, exchange=exchange,
                lastTradeDateOrContractMonth=expiry,
                multiplier=multiplier or "1",
            )
        case "OPTION":
            return Option(
                symbol=ticker, exchange=exchange or "SMART",
                lastTradeDateOrContractMonth=expiry,
                strike=strike, right=right,
                multiplier=multiplier or "100",
            )
        case _:
            return Stock(ticker, exchange, "USD")


async def _submit_to_ib(signal: TradeSignal, bracket: BracketOrder) -> TradeSignal:
    """Submit bracket order to Interactive Brokers.

    Uses an idempotency gate to prevent double-submission: if the same
    signal_id is already in-flight, the second call is a no-op.

    All three legs (parent, TP, SL) are placed in rapid succession
    within a single try block. If any leg fails after the parent was
    accepted, we attempt to cancel the parent to avoid orphans.
    """
    if signal.signal_id in _inflight_signals:
        log.warning(
            f"Duplicate submission blocked: {signal.signal_id} "
            f"for {bracket.ticker} is already in-flight"
        )
        signal.status = SignalStatus.EXECUTING
        return signal

    _inflight_signals.add(signal.signal_id)
    parent_trade = None

    try:
        from ib_async import LimitOrder, Order
        from execution_engine.broker.connection import get_ib

        ib = await get_ib()
        contract = _build_ib_contract(bracket.ticker)
        await ib.qualifyContractsAsync(contract)

        exit_action = "SELL" if bracket.action == "BUY" else "BUY"

        if bracket.order_type in (OrderType.TWAP, OrderType.VWAP):
            algo_type = "Twap" if bracket.order_type == OrderType.TWAP else "Vwap"
            parent_order = Order(
                action=bracket.action,
                totalQuantity=bracket.quantity,
                orderType="LMT",
                lmtPrice=bracket.limit_price,
                algoStrategy=algo_type,
                algoParams=[],
                transmit=False,
            )
            log.info(f"Using {algo_type} algo for {bracket.ticker} ({bracket.quantity} shares)")
        elif bracket.order_type == OrderType.PASSIVE_LIMIT:
            # IBKR Relative (REL) order: pegs to the near-touch (NBB for
            # buys, NBO for sells) with auxPrice=0 offset = pure post-only.
            # notHeld=True instructs IB not to auto-market if unfilled.
            parent_order = Order(
                action=bracket.action,
                totalQuantity=bracket.quantity,
                orderType="REL",
                auxPrice=0.0,
                lmtPrice=bracket.limit_price,  # ceiling (buy) / floor (sell) cap
                notHeld=True,
                transmit=False,
            )
            log.info(
                f"Using PASSIVE REL peg for {bracket.ticker} "
                f"({bracket.quantity} shares, cap=${bracket.limit_price:.2f})"
            )
        else:
            parent_order = LimitOrder(
                action=bracket.action,
                totalQuantity=bracket.quantity,
                lmtPrice=bracket.limit_price,
                transmit=False,
            )

        parent_trade = await asyncio.to_thread(ib.placeOrder, contract, parent_order)
        parent_id = parent_trade.order.orderId

        take_profit_order = LimitOrder(
            action=exit_action,
            totalQuantity=bracket.quantity,
            lmtPrice=bracket.take_profit_price,
            parentId=parent_id,
            transmit=False,
        )

        stop_loss_order = Order(
            action=exit_action,
            totalQuantity=bracket.quantity,
            orderType="STP",
            auxPrice=bracket.stop_loss_price,
            parentId=parent_id,
            transmit=True,
        )

        await asyncio.to_thread(ib.placeOrder, contract, take_profit_order)
        await asyncio.to_thread(ib.placeOrder, contract, stop_loss_order)

        signal.status = SignalStatus.EXECUTING
        log.info(
            f"Bracket order submitted for {bracket.ticker} "
            f"(parent={parent_id}, {bracket.order_type.value}, {bracket.quantity} shares)"
        )

        if bracket.order_type == OrderType.PASSIVE_LIMIT:
            asyncio.create_task(
                _passive_peg_monitor(ib, parent_trade, signal, bracket, contract)
            )

        return signal

    except Exception as exc:
        if parent_trade is not None:
            try:
                from execution_engine.broker.connection import get_ib
                ib = await get_ib()
                ib.cancelOrder(parent_trade.order)
                log.warning(
                    f"Cancelled orphan parent order for {bracket.ticker} "
                    f"after child leg failure: {exc}"
                )
            except Exception as cancel_exc:
                log.error(
                    f"CRITICAL: Failed to cancel orphan parent for "
                    f"{bracket.ticker}: {cancel_exc}. Manual intervention required."
                )
        _inflight_signals.discard(signal.signal_id)
        signal.status = SignalStatus.REJECTED
        raise IBKROrderRejectedError(0, str(exc)) from exc


async def _passive_peg_monitor(
    ib: Any,
    parent_trade: Any,
    signal: TradeSignal,
    bracket: BracketOrder,
    contract: Any,
    max_wait_sec: int = PASSIVE_PEG_TIMEOUT_SEC,
    poll_interval_sec: int = PASSIVE_PEG_POLL_INTERVAL_SEC,
) -> None:
    """Monitor a passive REL-peg parent order and cancel on timeout.

    The IBKR REL algo already auto-tracks the NBB/NBO, so this loop's
    main job is the timeout. If at T + max_wait_sec the parent still has
    not filled, we cancel it. We do NOT cross the spread — the signal
    would have been flagged tier3_shock upstream if it needed to.
    """
    deadline = dt.datetime.now(_ET) + dt.timedelta(seconds=max_wait_sec)
    start_ts = dt.datetime.now(_ET)

    try:
        while dt.datetime.now(_ET) < deadline:
            await asyncio.sleep(poll_interval_sec)

            order_status = getattr(parent_trade.orderStatus, "status", "") or ""
            filled_qty = getattr(parent_trade.orderStatus, "filled", 0) or 0
            remaining = getattr(parent_trade.orderStatus, "remaining", bracket.quantity)

            if order_status in ("Filled", "ApiCancelled", "Cancelled"):
                log.info(
                    f"[PASSIVE-PEG] {bracket.ticker} parent={parent_trade.order.orderId} "
                    f"resolved status={order_status} filled={filled_qty}/{bracket.quantity}"
                )
                return

        elapsed = (dt.datetime.now(_ET) - start_ts).total_seconds()
        log.warning(
            f"[PASSIVE-PEG] {bracket.ticker} unfilled after {elapsed:.0f}s — "
            f"cancelling parent={parent_trade.order.orderId} "
            f"(filled={getattr(parent_trade.orderStatus, 'filled', 0)}/"
            f"{bracket.quantity}). NOT crossing spread."
        )
        try:
            await asyncio.to_thread(ib.cancelOrder, parent_trade.order)
        except Exception as cancel_exc:
            log.error(
                f"[PASSIVE-PEG] cancel failed for {bracket.ticker}: {cancel_exc}"
            )
        signal.status = SignalStatus.CANCELLED
    except Exception as exc:
        log.error(f"[PASSIVE-PEG] monitor error for {bracket.ticker}: {exc}")


async def process_queued_orders(portfolio_equity: float) -> list[TradeSignal]:
    """Process orders that were queued during pre-market.

    Fetches live prices and computes Kelly sizing before resubmission.
    """
    if not _order_queue:
        return []

    if not is_regular_trading_hours():
        return []

    now_et = dt.datetime.now(_ET).time()
    if now_et < _SAFE_OPEN:
        log.info("Waiting for 09:45 ET to process queued orders")
        return []

    from execution_engine.math.kelly import optimal_allocation_for_signal

    processed: list[TradeSignal] = []
    while _order_queue:
        signal = _order_queue.pop(0)
        log.info(f"Resubmitting queued signal: {signal.action.value} {signal.ticker}")
        signal.status = SignalStatus.PENDING
        try:
            from data_primitives.extractors.openbb_client import fetch_current_price
            price = await fetch_current_price(signal.ticker)
            _, shares = optimal_allocation_for_signal(
                portfolio_equity=portfolio_equity,
                win_probability=signal.confidence,
                expected_return=0.05,
                volatility=0.2,
                current_price=price,
            )
            if shares == 0:
                signal.status = SignalStatus.CANCELLED
                log.info(f"Queued signal cancelled — Kelly too small for {signal.ticker}")
                processed.append(signal)
                continue
            result = await submit_signal(signal, portfolio_equity, price, shares)
            processed.append(result)
        except Exception as exc:
            log.error(f"Queued order resubmission failed for {signal.ticker}: {exc}")
            signal.status = SignalStatus.REJECTED
            processed.append(signal)

    return processed


def get_queued_orders() -> list[TradeSignal]:
    """Return current order queue."""
    return list(_order_queue)


# ── Futures & Options Order Submission ───────────────────────


async def submit_futures_order(
    symbol: str,
    action: str,
    quantity: int,
    exchange: str = "CME",
    expiry: str = "",
    multiplier: str = "50",
    limit_price: float | None = None,
    portfolio_equity: float = 1_000_000.0,
) -> dict:
    """Submit a futures order to IB.

    Paper mode fills immediately; live mode routes through IB.
    """
    contract_info = {
        "symbol": symbol, "action": action, "quantity": quantity,
        "exchange": exchange, "expiry": expiry, "multiplier": multiplier,
    }

    if settings.PAPER_TRADING:
        log.info(f"[PAPER] Futures {action} {quantity} {symbol} @ {exchange}")
        return {"status": "FILLED", **contract_info}

    try:
        from ib_async import LimitOrder, MarketOrder
        from execution_engine.broker.connection import get_ib

        ib = await get_ib()
        contract = _build_ib_contract(
            symbol, "FUTURE", exchange, expiry, multiplier=multiplier,
        )
        await ib.qualifyContractsAsync(contract)

        if limit_price:
            order = LimitOrder(action=action, totalQuantity=quantity, lmtPrice=limit_price)
        else:
            order = MarketOrder(action=action, totalQuantity=quantity)

        trade = await asyncio.to_thread(ib.placeOrder, contract, order)
        log.info(f"Futures order submitted: {action} {quantity} {symbol} (id={trade.order.orderId})")
        return {"status": "EXECUTING", "order_id": trade.order.orderId, **contract_info}
    except Exception as exc:
        log.error(f"Futures order failed for {symbol}: {exc}")
        return {"status": "REJECTED", "error": str(exc), **contract_info}


async def submit_options_order(
    underlying: str,
    expiry: str,
    strike: float,
    right: str,
    action: str,
    quantity: int,
    exchange: str = "SMART",
    limit_price: float | None = None,
) -> dict:
    """Submit an options order to IB.

    Paper mode fills immediately; live mode routes through IB.
    """
    contract_info = {
        "underlying": underlying, "expiry": expiry, "strike": strike,
        "right": right, "action": action, "quantity": quantity,
    }

    if settings.PAPER_TRADING:
        log.info(f"[PAPER] Option {action} {quantity} {underlying} {strike}{right[0]} {expiry}")
        return {"status": "FILLED", **contract_info}

    try:
        from ib_async import LimitOrder, MarketOrder
        from execution_engine.broker.connection import get_ib

        ib = await get_ib()
        contract = _build_ib_contract(
            underlying, "OPTION", exchange, expiry, strike, right,
        )
        await ib.qualifyContractsAsync(contract)

        if limit_price:
            order = LimitOrder(action=action, totalQuantity=quantity, lmtPrice=limit_price)
        else:
            order = MarketOrder(action=action, totalQuantity=quantity)

        trade = await asyncio.to_thread(ib.placeOrder, contract, order)
        log.info(f"Options order submitted: {action} {quantity} {underlying} {strike}{right[0]} (id={trade.order.orderId})")
        return {"status": "EXECUTING", "order_id": trade.order.orderId, **contract_info}
    except Exception as exc:
        log.error(f"Options order failed for {underlying}: {exc}")
        return {"status": "REJECTED", "error": str(exc), **contract_info}


async def submit_combo_order(
    legs: list[dict],
    action: str = "BUY",
    exchange: str = "SMART",
) -> dict:
    """Submit a multi-leg combo order (spreads, condors, etc.) to IB.

    Each leg dict should contain: underlying, expiry, strike, right, ratio, action.
    Paper mode fills immediately.
    """
    if settings.PAPER_TRADING:
        log.info(f"[PAPER] Combo order: {len(legs)} legs, net action={action}")
        return {"status": "FILLED", "legs": legs}

    try:
        from ib_async import ComboLeg, Contract, MarketOrder
        from execution_engine.broker.connection import get_ib

        ib = await get_ib()

        combo_legs: list[ComboLeg] = []
        for leg in legs:
            opt_contract = _build_ib_contract(
                leg["underlying"], "OPTION", exchange,
                leg["expiry"], leg["strike"], leg["right"],
            )
            await ib.qualifyContractsAsync(opt_contract)
            combo_legs.append(ComboLeg(
                conId=opt_contract.conId,
                ratio=leg.get("ratio", 1),
                action=leg["action"],
                exchange=exchange,
            ))

        combo = Contract(symbol=legs[0]["underlying"], secType="BAG",
                         exchange=exchange, currency="USD", comboLegs=combo_legs)
        order = MarketOrder(action=action, totalQuantity=1)
        trade = await asyncio.to_thread(ib.placeOrder, combo, order)
        log.info(f"Combo order submitted: {len(legs)} legs (id={trade.order.orderId})")
        return {"status": "EXECUTING", "order_id": trade.order.orderId, "legs": legs}
    except Exception as exc:
        log.error(f"Combo order failed: {exc}")
        return {"status": "REJECTED", "error": str(exc), "legs": legs}
