"""Supervisor / Consensus node — final gate before execution.

Routes the LangGraph DAG: if the auditor approved with sufficient confidence,
emit a trade signal. Otherwise, loop back or terminate.
"""
from __future__ import annotations

import datetime as dt
import uuid

from shared.logger import get_logger
from shared.types import AlphaHorizon, SignalStatus, TradeAction, TradeSignal
from graph_intelligence.graph_state.state import CausalArbitrageState

log = get_logger("agent.supervisor")

_MIN_CONFIDENCE = 0.55
_MAX_ITERATIONS = 4


def should_continue(state: CausalArbitrageState) -> str:
    """LangGraph conditional edge: decide whether to continue, retry, or end.

    Returns:
        "execute"  — confidence high enough, route to signal emission
        "retry"    — below threshold but iterations remain
        "end"      — max iterations reached or explicitly HOLD
    """
    action = state.get("proposed_action")
    confidence = state.get("confidence", 0.0)
    iteration = state.get("iteration", 0)

    if action == "HOLD" or confidence == 0.0:
        log.info("Supervisor: HOLD — no trade")
        return "end"

    if confidence >= _MIN_CONFIDENCE:
        log.info(
            f"Supervisor: APPROVED {action} at {confidence:.2f} — routing to execution"
        )
        return "execute"

    if iteration < _MAX_ITERATIONS:
        log.info(
            f"Supervisor: confidence {confidence:.2f} < {_MIN_CONFIDENCE}, "
            f"iteration {iteration}/{_MAX_ITERATIONS} — retrying"
        )
        return "retry"

    log.info("Supervisor: max iterations reached — terminating")
    return "end"


def emit_trade_signal(state: CausalArbitrageState) -> TradeSignal | None:
    """Convert finalized state into a TradeSignal for the execution engine."""
    action_str = state.get("proposed_action")
    if not action_str or action_str == "HOLD":
        return None

    target = state.get("target_ticker", "")
    if not target:
        return None

    horizon_str = state.get("alpha_horizon", "3rd_order")
    try:
        horizon = AlphaHorizon(horizon_str)
    except ValueError:
        horizon = AlphaHorizon.THIRD_ORDER

    causal_paths = state.get("causal_paths", [])
    path_tickers: list[str] = []
    compound_risk = 0.0
    if causal_paths:
        best = causal_paths[0]
        path_tickers = best.get("path_tickers", [])
        compound_risk = best.get("compound_risk", 0.0)

    signal = TradeSignal(
        signal_id=str(uuid.uuid4())[:8],
        timestamp=dt.datetime.now(dt.timezone.utc),
        ticker=target,
        action=TradeAction(action_str),
        alpha_horizon=horizon,
        confidence=state.get("confidence", 0.0),
        rationale=state.get("rationale", ""),
        compound_risk=compound_risk,
        causal_path=path_tickers,
        status=SignalStatus.PENDING,
    )

    log.info(
        f"Signal emitted: [bold]{signal.action.value} {signal.ticker}[/bold] "
        f"(conf={signal.confidence:.2f}, horizon={signal.alpha_horizon.value})"
    )
    return signal
