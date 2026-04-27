"""Supervisor / Consensus node — final gate before execution.

Routes the LangGraph DAG: if the auditor approved with sufficient confidence,
emit a trade signal. Otherwise, loop back or terminate.
"""

from __future__ import annotations

import datetime as dt
import uuid

from graph_intelligence.graph_state.state import CausalArbitrageState
from shared.logger import get_logger
from shared.types import (
    AlphaHorizon,
    AlphaScore,
    AssetClass,
    InstrumentChoice,
    SignalStatus,
    SizingMethod,
    TradeAction,
    TradeIntent,
)

log = get_logger("agent.supervisor")

from shared.config import settings as _settings

_MIN_CONFIDENCE: float = _settings.SUPERVISOR_MIN_CONFIDENCE
# P2-30: iteration cap is now a setting so a deployment can bump it
# (e.g. for a stricter auditor that burns more retries before
# converging) without code change. Default preserves prior behaviour.
_MAX_ITERATIONS: int = getattr(_settings, "SUPERVISOR_MAX_ITERATIONS", 4)


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
        log.info(f"Supervisor: APPROVED {action} at {confidence:.2f} — routing to execution")
        return "execute"

    if iteration < _MAX_ITERATIONS:
        log.info(
            f"Supervisor: confidence {confidence:.2f} < {_MIN_CONFIDENCE}, "
            f"iteration {iteration}/{_MAX_ITERATIONS} — retrying"
        )
        return "retry"

    log.info("Supervisor: max iterations reached — terminating")
    return "end"


def emit_trade_signal(state: CausalArbitrageState) -> TradeIntent | None:
    """Convert finalized LangGraph state into a `TradeIntent`.

    Phase-3: replaces the legacy `TradeSignal` emission. The intent
    carries the debate / analyst audit trail from the LangGraph run so
    the downstream post-pipeline orchestrator can reuse it without
    re-reasoning from scratch.
    """
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
    compound_risk: float | None = None
    if causal_paths:
        best = causal_paths[0]
        path_tickers = best.get("path_tickers", [])
        compound_risk = best.get("compound_risk", 0.0)

    try:
        direction = TradeAction(action_str)
    except ValueError:
        direction = TradeAction.HOLD

    conviction = float(state.get("confidence", 0.0))

    # P2-24: expected_r was hardcoded to 2.0 on every emitted intent.
    # That number then surfaced in logs, the dashboard, and downstream
    # cost-model EV hurdles — all claiming R=2 regardless of the
    # scenario. We now prefer, in order:
    #   1. supervisor-emitted `strategy_parameters.expected_r` when
    #      present (populated by the supervisor_node from priors)
    #   2. a conservative data-driven estimate from `compound_risk`
    #      (more dependency => higher expected move), clamped
    #   3. fall back to 1.5 (the minimum acceptable R for the EV gate)
    strategy_params = state.get("strategy_parameters") or {}
    param_er = strategy_params.get("expected_r") if isinstance(strategy_params, dict) else None
    if isinstance(param_er, (int, float)) and param_er > 0:
        expected_r = float(param_er)
    elif compound_risk is not None and compound_risk > 0:
        # More compound-risk ≈ more downstream-beta on the shock ≈
        # larger expected move. Clamp so a hallucinated compound_risk
        # can't drive R past a reasonable ceiling.
        expected_r = max(1.5, min(3.0, 1.5 + float(compound_risk) * 1.5))
    else:
        expected_r = 1.5

    min_r = max(1.0, expected_r * 0.75)

    # The LangGraph pipeline produced this via the debate + auditor
    # loop. Treat the pipeline confidence as both the factor score and
    # the Bayesian conviction — the post-pipeline orchestrator will
    # compose a fresh AlphaScore from the full DecisionContext
    # (regime + technicals + causal intel) and may refine this value
    # before final gating.
    alpha = AlphaScore(
        ticker=target,
        direction=direction,
        factor_score=conviction,
        bayesian_conviction=conviction,
        unified_conviction=conviction,
        expected_r=expected_r,
        min_r=min_r,
        factor_breakdown={
            "pipeline_confidence": conviction,
            "compound_risk": float(compound_risk or 0.0),
        },
        rationale=f"LangGraph pipeline consensus at {conviction:.2f}",
    )

    intent = TradeIntent(
        intent_id=str(uuid.uuid4())[:8],
        timestamp=dt.datetime.now(dt.UTC),
        execute=conviction >= _MIN_CONFIDENCE,
        ticker=target,
        direction=direction,
        asset_class=AssetClass.EQUITY,
        instrument_choice=InstrumentChoice.EQUITY,
        alpha_horizon=horizon,
        alpha_score=alpha,
        sizing_method=SizingMethod.KELLY_EQUITY,
        rationale=state.get("rationale", ""),
        causal_path=path_tickers,
        compound_risk=compound_risk,
        graph_intel=True,
        audit_trail={
            "debate_result": state.get("debate_result", {}),
            "analyst_opinions": state.get("analyst_opinions", []),
            "researcher_consensus": state.get("researcher_consensus", {}),
            "forbidden_strategies": state.get("forbidden_strategies", []),
            "strategy_parameters": state.get("strategy_parameters", {}),
            "risk_multiplier": state.get("risk_multiplier", 1.0),
            "causal_intel": state.get("causal_intel", {}),
            "pipeline_iteration": state.get("iteration", 0),
        },
        status=SignalStatus.PENDING,
    )

    log.info(
        f"Intent emitted: [bold]{intent.direction.value} {intent.ticker}[/bold] "
        f"(conv={conviction:.2f}, horizon={intent.alpha_horizon.value})"
    )
    return intent
