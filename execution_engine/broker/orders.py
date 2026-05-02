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
import threading
from typing import Any
from zoneinfo import ZoneInfo

from execution_engine.broker.liquidity import (
    LiquidityTier,
    compute_pre_market_midpoint,
)
from execution_engine.broker.liquidity import (
    classify as classify_liquidity,
)
from shared.config import settings
from shared.exceptions import IBKROrderRejectedError, PreMarketTrapError  # noqa: F401
from shared.executors import run_in_io, with_ib_deadline
from shared.logger import get_logger
from shared.types import (
    AlphaHorizon,
    AssetClass,
    BracketOrder,
    OrderType,
    SignalStatus,
    TradeAction,
    TradeSignal,
)

log = get_logger("broker.orders")

# All session windows and passive-routing cadence knobs live in
# `shared.config.Settings` so a deployment can override via .env without
# touching code. The module-level tuples below are derived once at
# import time from Settings so downstream helpers (is_regular_trading_hours,
# etc.) stay pure-Python time comparisons.
_ET = ZoneInfo("America/New_York")
_RTH_OPEN = dt.time(settings.RTH_OPEN_HOUR, settings.RTH_OPEN_MINUTE)
_RTH_CLOSE = dt.time(settings.RTH_CLOSE_HOUR, settings.RTH_CLOSE_MINUTE)
_SAFE_OPEN = dt.time(settings.SAFE_OPEN_HOUR, settings.SAFE_OPEN_MINUTE)
# Pre-market window. IBKR pre-market opens at 04:00 ET; we only attempt
# the midpoint-LIMIT path within the last 90 minutes before RTH where
# both bid/ask depth is materially better and our quote vendor still
# has fresh data (most providers throttle at 03:00-07:00 ET).
_PRE_MARKET_LIMIT_OPEN = dt.time(
    settings.PRE_MARKET_LIMIT_OPEN_HOUR,
    settings.PRE_MARKET_LIMIT_OPEN_MINUTE,
)

# Passive-routing knobs (Tier-1 microstructure).
PASSIVE_PEG_TIMEOUT_SEC: int = settings.PASSIVE_PEG_TIMEOUT_SEC
PASSIVE_PEG_POLL_INTERVAL_SEC: int = settings.PASSIVE_PEG_POLL_INTERVAL_SEC
PASSIVE_REPEG_TICK_THRESHOLD = 2  # re-peg when quote drifts >N ticks

# P1-D: both structures are mutated from async coroutines that may be
# scheduled across OS threads (ib_async dispatches event callbacks from
# its own thread). A `threading.Lock` keeps mutations atomic even in
# the worst case; the critical sections are pure dict / list / set ops
# so contention is sub-microsecond.
_broker_state_lock = threading.Lock()
_order_queue: list[TradeSignal] = []
_inflight_signals: set[str] = set()


def is_regular_trading_hours() -> bool:
    """Check if we're within Regular Trading Hours (09:30–16:00 ET)."""
    now_et = dt.datetime.now(_ET).time()
    return _RTH_OPEN <= now_et <= _RTH_CLOSE


def is_pre_market_window() -> bool:
    """Pre-market window where the midpoint-LIMIT path is eligible."""
    now_et = dt.datetime.now(_ET).time()
    return _PRE_MARKET_LIMIT_OPEN <= now_et < _RTH_OPEN


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


def _load_bars_for_bracket(ticker: str) -> list:
    """Fetch the recent bar window needed for ATR computation.

    Batch-1/B: tries the orchestrator in-process price cache first
    (populated by the ingestion cycle with ~90 days of history), then
    falls back to the local data store loader used by the backtest
    engine. Returns an empty list when neither path has data — the
    caller's ATR helper will then use the fixed-pct fallback.
    """
    try:
        import backend.services.orchestrator as orch

        cached = orch._price_cache.get(ticker)
        if cached and len(cached) >= 15:
            return cached[-60:]
    except Exception:
        pass
    try:
        from backtesting.engine import _load_local_bars

        bars = _load_local_bars(ticker)
        if bars and len(bars) >= 15:
            return bars[-60:]
    except Exception:
        pass
    return []


# ── Batch-1/B: ATR-scaled dynamic bracket ────────────────────────────


def _target_r_for_signal(signal: TradeSignal) -> float:
    """Select the R-ratio for this signal's horizon / shock state.

    Tier-3 shocks get the tightest R (BRACKET_TARGET_R_TIER3_SHOCK)
    because the alpha decays fast. 3rd / 4th order structural trades
    get BRACKET_TARGET_R_LONG_HORIZON so the thesis has room to play
    out through day-to-day noise. Everything else gets the default.
    """
    if getattr(signal, "tier3_shock", False):
        return float(settings.BRACKET_TARGET_R_TIER3_SHOCK)
    if signal.alpha_horizon in (AlphaHorizon.THIRD_ORDER, AlphaHorizon.FOURTH_ORDER):
        return float(settings.BRACKET_TARGET_R_LONG_HORIZON)
    return float(settings.BRACKET_TARGET_R)


def compute_bracket_levels(
    signal: TradeSignal,
    entry_price: float,
    bars: list | None = None,
) -> tuple[float, float, dict]:
    """Return (stop_loss_price, take_profit_price, meta) for this signal.

    Batch-1/B: replaces the fixed ``BRACKET_STOP_LOSS_PCT`` /
    ``BRACKET_TAKE_PROFIT_PCT`` math with an ATR-scaled bracket so
    stops adapt to each instrument's realised volatility:

        atr14       = ATR(14) on the provided bars
        stop_pct    = clamp(K_stop * atr14 / entry_price,
                            MIN_STOP_PCT, MAX_STOP_PCT)
        tp_pct      = target_R * stop_pct     (target_R picks by horizon
                                               / shock state via
                                               _target_r_for_signal)

    In high-vol regimes the stop widens with the market so the
    thesis isn't cashed out on noise; in low-vol regimes the stop
    tightens so losses cap before theta-decay on F&O overwhelms the
    edge. The R-ratio stays constant, only the absolute distance
    scales.

    Fallback: when no bars are available (pre-market quote, new
    listing, ingestion miss) we return the legacy fixed-pct bracket
    so the code path stays complete. The ``meta`` dict records
    whether we took the ATR path or fell back.
    """
    from execution_engine.math._primitives import compute_atr

    target_r = _target_r_for_signal(signal)

    # Fallback path — no usable history for ATR. Penny stocks (price
    # below $0.50) are routed here too; ATR divisions on near-zero
    # prices produce absurd stop multipliers, and IB rejects most
    # orders on sub-$1 names anyway.
    if not bars or len(bars) < 15 or entry_price <= 0.50:
        legacy_stop = float(settings.BRACKET_STOP_LOSS_PCT)
        legacy_tp = float(
            settings.BRACKET_TAKE_PROFIT_PCT_LONG_HORIZON
            if signal.alpha_horizon in (AlphaHorizon.THIRD_ORDER, AlphaHorizon.FOURTH_ORDER)
            else settings.BRACKET_TAKE_PROFIT_PCT
        )
        sl, tp = _project_bracket_prices(
            entry_price,
            signal.action,
            legacy_stop,
            legacy_tp,
        )
        return (
            sl,
            tp,
            {
                "mode": "fallback_fixed_pct",
                "stop_pct": legacy_stop,
                "tp_pct": legacy_tp,
                "target_r": legacy_tp / legacy_stop if legacy_stop > 0 else 0.0,
                "atr_pct": None,
            },
        )

    import numpy as np

    highs = np.array([b.high for b in bars], dtype=float)
    lows = np.array([b.low for b in bars], dtype=float)
    closes = np.array([b.close for b in bars], dtype=float)
    atr14 = float(compute_atr(highs, lows, closes, period=14))
    if atr14 <= 0 or not np.isfinite(atr14):
        # Degenerate data → fall back so we never emit a zero-distance bracket.
        return compute_bracket_levels(signal, entry_price, bars=None)

    atr_pct = atr14 / entry_price
    raw_stop_pct = float(settings.ATR_STOP_MULTIPLIER) * atr_pct
    stop_pct = max(
        float(settings.MIN_STOP_PCT),
        min(float(settings.MAX_STOP_PCT), raw_stop_pct),
    )
    tp_pct = target_r * stop_pct

    sl, tp = _project_bracket_prices(entry_price, signal.action, stop_pct, tp_pct)
    return (
        sl,
        tp,
        {
            "mode": "atr_scaled",
            "atr14": atr14,
            "atr_pct": atr_pct,
            "stop_pct": stop_pct,
            "tp_pct": tp_pct,
            "target_r": target_r,
            "k_stop": float(settings.ATR_STOP_MULTIPLIER),
        },
    )


def _project_bracket_prices(
    entry: float,
    action: TradeAction,
    stop_pct: float,
    tp_pct: float,
) -> tuple[float, float]:
    """Return (stop_loss, take_profit) prices for the direction."""
    if action == TradeAction.SHORT:
        sl = entry * (1 + stop_pct)
        tp = entry * (1 - tp_pct)
    else:
        sl = entry * (1 - stop_pct)
        tp = entry * (1 + tp_pct)
    return round(sl, 2), round(tp, 2)


async def submit_signal(
    signal: TradeSignal,
    portfolio_equity: float,
    current_price: float,
    quantity: int,
) -> TradeSignal:
    """Process a trade signal through the pre-market and order routing logic.

    Pre-market routing (PDF spec):
      * LIQUID name (e.g. S&P 500 class)  → submit a stringent LIMIT at
        the pre-market spread midpoint. Accept non-fill risk for price
        protection.
      * ILLIQUID name (mid/small-cap)     → delay to 09:45 ET via the
        existing queue path.

    Liquidity tier is computed dynamically from local OHLCV
    (``execution_engine.broker.liquidity``); no hardcoded ticker list.
    """

    if not is_regular_trading_hours():
        # Try the liquid → midpoint LIMIT path first.
        if is_pre_market_window() and signal.asset_class == AssetClass.EQUITY:
            tier, meta = classify_liquidity(signal.ticker)
            if tier == LiquidityTier.LIQUID:
                midpoint = await compute_pre_market_midpoint(signal.ticker)
                if midpoint and midpoint > 0:
                    log.info(
                        f"Pre-market LIMIT @ midpoint for LIQUID {signal.ticker} "
                        f"(${midpoint:.2f}, dv≈${meta.get('median_dollar_volume', 0):,.0f}, "
                        f"spread≈{meta.get('median_spread_bps', 0):.1f}bps) — "
                        f"NON-fill acceptable"
                    )
                    return await _execute_now(signal, midpoint, quantity, force_limit=True)
                log.info(f"LIQUID {signal.ticker} but no pre-market quote — falling back to delay")

        now_et = dt.datetime.now(_ET)
        log.warning(
            f"Signal for {signal.ticker} generated outside RTH "
            f"({now_et.strftime('%H:%M')} ET) — queueing for 09:45 ET"
        )
        signal.status = SignalStatus.QUEUED
        with _broker_state_lock:
            _order_queue.append(signal)
        return signal

    return await _execute_now(signal, current_price, quantity, force_limit=False)


async def _execute_now(
    signal: TradeSignal,
    price: float,
    quantity: int,
    *,
    force_limit: bool = False,
) -> TradeSignal:
    """Build the bracket and dispatch through the right asset-class path.

    ``force_limit=True`` is used by the pre-market liquid path: we want a
    crossable LIMIT (not the passive REL peg the horizon would otherwise
    pick), because the entire point of submitting in pre-market is to
    take the midpoint while it's still there.

    Batch-1/B: the stop/TP levels come from ``compute_bracket_levels``,
    which returns ATR-scaled distances when OHLCV bars are available
    and falls back to the fixed-pct legacy bracket otherwise.
    """
    order_type = OrderType.LIMIT if force_limit else _determine_order_type(signal)
    ib_action = "SELL" if signal.action == TradeAction.SHORT else "BUY"

    # Pull the ticker's recent history for ATR.
    # We go via the orchestrator's price cache first because it is
    # already populated by the ingestion cycle; a cache miss falls
    # through to a local-disk load and, finally, to the fixed-pct
    # fallback inside compute_bracket_levels.
    bars = _load_bars_for_bracket(signal.ticker)
    stop_loss, take_profit, bracket_meta = compute_bracket_levels(
        signal,
        price,
        bars,
    )

    bracket = BracketOrder(
        ticker=signal.ticker,
        action=ib_action,
        quantity=quantity,
        order_type=order_type,
        limit_price=price,
        take_profit_price=take_profit,
        stop_loss_price=stop_loss,
    )

    # IBKR Paper tradeable gate — crypto is signal-only (IBKR Paper
    # doesn't support Paxos). FX, futures, equities, ETFs are all
    # supported on IBKR Paper.
    _IBKR_PAPER_UNSUPPORTED = {AssetClass.CRYPTO}
    if settings.PAPER_TRADING and signal.asset_class in _IBKR_PAPER_UNSUPPORTED:
        signal.status = SignalStatus.FILLED
        log.info(
            f"[SIGNAL-ONLY] {ib_action} {quantity} {signal.ticker} "
            f"({signal.asset_class.value}) @ ${price:.2f} | "
            f"IBKR Paper does not support {signal.asset_class.value} — "
            f"recording signal for tracking only"
        )
        return signal

    if settings.PAPER_TRADING:
        signal.status = SignalStatus.FILLED
        log.info(
            f"[PAPER] {ib_action} {quantity} {signal.ticker} "
            f"({signal.asset_class.value}) @ ${price:.2f} | "
            f"TP=${bracket.take_profit_price:.2f} SL=${bracket.stop_loss_price:.2f} | "
            f"Type={order_type.value} | "
            f"bracket={bracket_meta.get('mode')} "
            f"stop_pct={bracket_meta.get('stop_pct', 0):.3%} "
            f"target_r={bracket_meta.get('target_r', 0):.2f}"
        )
        return signal

    # Asset-class dispatch — futures & options have their own bracket-less
    # IB paths, equity (default) goes through the bracket submitter.
    if signal.asset_class == AssetClass.FUTURE:
        result = await submit_futures_order(
            symbol=signal.ticker,
            action=ib_action,
            quantity=quantity,
            exchange=signal.exchange or "CME",
            expiry=signal.contract_expiry or "",
            multiplier=signal.contract_multiplier or "50",
            limit_price=price if order_type != OrderType.TWAP else None,
            portfolio_equity=0.0,
        )
        signal.status = (
            SignalStatus.EXECUTING
            if result.get("status") == "EXECUTING"
            else SignalStatus.FILLED
            if result.get("status") == "FILLED"
            else SignalStatus.REJECTED
        )
        return signal
    if signal.asset_class == AssetClass.OPTION:
        if (
            not signal.contract_expiry
            or signal.contract_strike is None
            or not signal.contract_right
        ):
            signal.status = SignalStatus.REJECTED
            log.error(
                f"OPTION signal for {signal.ticker} missing expiry / strike / right "
                f"— refusing to submit"
            )
            return signal
        result = await submit_options_order(
            underlying=signal.ticker,
            expiry=signal.contract_expiry,
            strike=float(signal.contract_strike),
            right=signal.contract_right,
            action=ib_action,
            quantity=quantity,
            exchange=signal.exchange or "SMART",
            limit_price=price,
        )
        signal.status = (
            SignalStatus.EXECUTING
            if result.get("status") == "EXECUTING"
            else SignalStatus.FILLED
            if result.get("status") == "FILLED"
            else SignalStatus.REJECTED
        )
        return signal

    return await _submit_to_ib(signal, bracket)


def _build_ib_contract(
    ticker: str,
    asset_class: str = "EQUITY",
    exchange: str = "SMART",
    expiry: str = "",
    strike: float = 0.0,
    right: str = "",
    multiplier: str = "",
    currency: str = "USD",
):
    """Build the appropriate ib_async contract for any asset class.

    Accepts either the ``AssetClass`` enum value (e.g. ``"EQUITY"``,
    ``"FUTURE"``, ``"OPTION"``, ``"INDEX"``, ``"FX"``, ``"CRYPTO"``) or
    its lowercased form. Unknown values fall back to ``Stock`` so the
    legacy equity path is never broken.
    """
    from ib_async import Future, Option, Stock

    cls = (asset_class or "EQUITY").upper()

    match cls:
        case "FUTURE":
            return Future(
                symbol=ticker,
                exchange=exchange,
                lastTradeDateOrContractMonth=expiry,
                multiplier=multiplier or "1",
                currency=currency,
            )
        case "OPTION":
            return Option(
                symbol=ticker,
                exchange=exchange or "SMART",
                lastTradeDateOrContractMonth=expiry,
                strike=strike,
                right=right,
                multiplier=multiplier or "100",
                currency=currency,
            )
        case "INDEX":
            try:
                from ib_async import Index

                return Index(symbol=ticker, exchange=exchange or "CBOE", currency=currency)
            except ImportError:
                return Stock(ticker, exchange or "SMART", currency)
        case "FX":
            try:
                from ib_async import Forex

                return Forex(pair=ticker)
            except ImportError:
                return Stock(ticker, exchange or "IDEALPRO", currency)
        case "CRYPTO":
            try:
                from ib_async import Crypto

                return Crypto(symbol=ticker, exchange=exchange or "PAXOS", currency=currency)
            except ImportError:
                return Stock(ticker, exchange or "PAXOS", currency)
        case _:
            return Stock(ticker, exchange or "SMART", currency)


async def _submit_to_ib(signal: TradeSignal, bracket: BracketOrder) -> TradeSignal:
    """Submit bracket order to Interactive Brokers.

    Uses an idempotency gate to prevent double-submission: if the same
    signal_id is already in-flight, the second call is a no-op.

    All three legs (parent, TP, SL) are placed in rapid succession
    within a single try block. If any leg fails after the parent was
    accepted, we attempt to cancel the parent to avoid orphans.

    **P1-D hardening.** The inflight set is protected by a module-level
    lock, and cleanup lives in a ``finally`` so the entry is removed on
    every exit path (success, raise, cancel). Before this fix a
    successful bracket left the signal_id permanently in the set,
    which silently short-circuited any subsequent retry attempt at
    L332-337 (`Duplicate submission blocked`).
    """
    with _broker_state_lock:
        if signal.signal_id in _inflight_signals:
            log.warning(
                f"Duplicate submission blocked: {signal.signal_id} "
                f"for {bracket.ticker} is already in-flight"
            )
            signal.status = SignalStatus.EXECUTING
            return signal
        _inflight_signals.add(signal.signal_id)

    parent_trade = None
    submission_ok = False

    try:
        from ib_async import LimitOrder, Order

        from execution_engine.broker.connection import get_ib

        ib = await get_ib()
        contract = _build_ib_contract(
            bracket.ticker,
            asset_class=signal.asset_class.value if hasattr(signal, "asset_class") else "EQUITY",
            exchange=getattr(signal, "exchange", "SMART") or "SMART",
            currency=getattr(signal, "currency", "USD") or "USD",
        )
        await with_ib_deadline(
            ib.qualifyContractsAsync(contract),
            deadline_ms=1500,
            op_name=f"qualifyContracts({bracket.ticker})",
        )

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

        # placeOrder is synchronous in ib_async — push to the dedicated I/O
        # pool so the IB event loop never blocks even on a slow ack.
        parent_trade = await run_in_io(ib.placeOrder, contract, parent_order)
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

        await run_in_io(ib.placeOrder, contract, take_profit_order)
        await run_in_io(ib.placeOrder, contract, stop_loss_order)

        signal.status = SignalStatus.EXECUTING
        log.info(
            f"Bracket order submitted for {bracket.ticker} "
            f"(parent={parent_id}, {bracket.order_type.value}, {bracket.quantity} shares)"
        )

        if bracket.order_type == OrderType.PASSIVE_LIMIT:
            asyncio.create_task(_passive_peg_monitor(ib, parent_trade, signal, bracket, contract))

        submission_ok = True
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
        signal.status = SignalStatus.REJECTED
        raise IBKROrderRejectedError(0, str(exc)) from exc
    finally:
        # P1-D: always release the inflight slot — success AND failure
        # paths. Previous implementation only discarded on exception,
        # which meant a successful bracket locked the signal_id forever
        # and silently no-op'd all subsequent submits with that id.
        # For passive REL pegs that need async monitoring, the peg
        # monitor task is already scheduled above and carries its own
        # state; nothing here cancels it.
        if not submission_ok:
            with _broker_state_lock:
                _inflight_signals.discard(signal.signal_id)
        else:
            # Equity LIMIT / TWAP / VWAP paths transmit terminal state
            # through ib_async callbacks. We release after the bracket
            # is fully placed so retries on a later cycle can proceed.
            # PASSIVE_LIMIT monitors parent resolution in the background
            # and MUST NOT be cleared here — the monitor owns the slot
            # until timeout or fill.
            if bracket.order_type != OrderType.PASSIVE_LIMIT:
                with _broker_state_lock:
                    _inflight_signals.discard(signal.signal_id)


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
                with _broker_state_lock:
                    _inflight_signals.discard(signal.signal_id)
                return

        elapsed = (dt.datetime.now(_ET) - start_ts).total_seconds()
        log.warning(
            f"[PASSIVE-PEG] {bracket.ticker} unfilled after {elapsed:.0f}s — "
            f"cancelling parent={parent_trade.order.orderId} "
            f"(filled={getattr(parent_trade.orderStatus, 'filled', 0)}/"
            f"{bracket.quantity}). NOT crossing spread."
        )
        try:
            await run_in_io(ib.cancelOrder, parent_trade.order)
        except Exception as cancel_exc:
            log.error(f"[PASSIVE-PEG] cancel failed for {bracket.ticker}: {cancel_exc}")
        signal.status = SignalStatus.CANCELLED
    except Exception as exc:
        log.error(f"[PASSIVE-PEG] monitor error for {bracket.ticker}: {exc}")
    finally:
        # Release the inflight slot on every exit path so a later
        # cycle can retry this signal_id if needed.
        with _broker_state_lock:
            _inflight_signals.discard(signal.signal_id)


async def process_queued_orders(portfolio_equity: float) -> list[TradeSignal]:
    """Process orders that were queued during pre-market.

    Fetches live prices and computes Kelly sizing before resubmission.
    """
    with _broker_state_lock:
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
    while True:
        with _broker_state_lock:
            if not _order_queue:
                break
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
                # Batch-1/D: conviction-scaled sizing on the queued-order
                # resubmission path too, so a 0.62 signal doesn't get
                # full size simply because it was deferred past RTH.
                conviction=float(signal.confidence),
                asset_class=signal.asset_class.value,
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
    """Return a snapshot of the current order queue (lock-protected copy)."""
    with _broker_state_lock:
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
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "exchange": exchange,
        "expiry": expiry,
        "multiplier": multiplier,
    }

    if settings.PAPER_TRADING:
        log.info(f"[PAPER] Futures {action} {quantity} {symbol} @ {exchange}")
        return {"status": "FILLED", **contract_info}

    try:
        from ib_async import LimitOrder, MarketOrder

        from execution_engine.broker.connection import get_ib

        ib = await get_ib()
        contract = _build_ib_contract(
            symbol,
            "FUTURE",
            exchange,
            expiry,
            multiplier=multiplier,
        )
        await ib.qualifyContractsAsync(contract)

        if limit_price:
            order = LimitOrder(action=action, totalQuantity=quantity, lmtPrice=limit_price)
        else:
            order = MarketOrder(action=action, totalQuantity=quantity)

        trade = await run_in_io(ib.placeOrder, contract, order)
        log.info(
            f"Futures order submitted: {action} {quantity} {symbol} (id={trade.order.orderId})"
        )
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
        "underlying": underlying,
        "expiry": expiry,
        "strike": strike,
        "right": right,
        "action": action,
        "quantity": quantity,
    }

    if settings.PAPER_TRADING:
        log.info(f"[PAPER] Option {action} {quantity} {underlying} {strike}{right[0]} {expiry}")
        return {"status": "FILLED", **contract_info}

    try:
        from ib_async import LimitOrder, MarketOrder

        from execution_engine.broker.connection import get_ib

        ib = await get_ib()
        contract = _build_ib_contract(
            underlying,
            "OPTION",
            exchange,
            expiry,
            strike,
            right,
        )
        await ib.qualifyContractsAsync(contract)

        if limit_price:
            order = LimitOrder(action=action, totalQuantity=quantity, lmtPrice=limit_price)
        else:
            order = MarketOrder(action=action, totalQuantity=quantity)

        trade = await run_in_io(ib.placeOrder, contract, order)
        log.info(
            f"Options order submitted: {action} {quantity} {underlying} {strike}{right[0]} (id={trade.order.orderId})"
        )
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
                leg["underlying"],
                "OPTION",
                exchange,
                leg["expiry"],
                leg["strike"],
                leg["right"],
            )
            await ib.qualifyContractsAsync(opt_contract)
            combo_legs.append(
                ComboLeg(
                    conId=opt_contract.conId,
                    ratio=leg.get("ratio", 1),
                    action=leg["action"],
                    exchange=exchange,
                )
            )

        combo = Contract(
            symbol=legs[0]["underlying"],
            secType="BAG",
            exchange=exchange,
            currency="USD",
            comboLegs=combo_legs,
        )
        order = MarketOrder(action=action, totalQuantity=1)
        trade = await run_in_io(ib.placeOrder, combo, order)
        log.info(f"Combo order submitted: {len(legs)} legs (id={trade.order.orderId})")
        return {"status": "EXECUTING", "order_id": trade.order.orderId, "legs": legs}
    except Exception as exc:
        log.error(f"Combo order failed: {exc}")
        return {"status": "REJECTED", "error": str(exc), "legs": legs}
