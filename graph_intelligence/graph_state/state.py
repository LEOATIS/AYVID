"""LangGraph state definitions for the N-th Order Causal Arbitrage pipeline.

The state object flows through the DAG maintaining strict chronological integrity.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage

from shared.types import CausalEvent


class CausalArbitrageState(TypedDict):
    """Master state passed between all LangGraph nodes.

    Fields:
        messages: LangGraph message accumulator for agent communication.
        shock_ticker: The ticker that originated the supply chain shock.
        target_ticker: The downstream ticker being evaluated for a trade.
        timeline: Chronologically sorted compressed events (Semantic Compression output).
        causal_paths: Discovered N-th order paths from shock to target.
        graph_topology_json: Stringified JSON of the Cypher path traversal results.
        proposed_action: The final trade recommendation.
        rationale: LLM-generated reasoning for the proposed action.
        confidence: Model confidence in the trade signal (0.0 to 1.0).
        alpha_horizon: Estimated time horizon for the alpha to materialize.
        risk_assessment: Risk analysis from the auditor agent.
        sequence_id: Latest event-sourcing sequence ID for validation.
        iteration: Current reasoning iteration (prevents infinite loops).
        analyst_opinions: Structured opinions from the 4 specialist analyst agents.
        debate_result: Aggregated debate outcome (consensus, conviction, votes).
        quant_data: Serialized quantitative signal metrics (Hurst, OU, ATR, regime).
    """
    messages: Annotated[list[BaseMessage], add_messages]
    shock_ticker: str
    target_ticker: str
    timeline: list[dict]
    causal_paths: list[dict]
    graph_topology_json: str
    proposed_action: str | None
    rationale: str | None
    confidence: float
    alpha_horizon: str | None
    risk_assessment: str | None
    sequence_id: int
    iteration: int
    analyst_opinions: list[dict]
    debate_result: dict
    quant_data: str


def create_initial_state(
    shock_ticker: str,
    target_ticker: str = "",
    timeline: list[CausalEvent] | None = None,
    sequence_id: int = 0,
) -> CausalArbitrageState:
    """Factory to build a clean initial state for a new causal analysis run."""
    return CausalArbitrageState(
        messages=[],
        shock_ticker=shock_ticker,
        target_ticker=target_ticker,
        timeline=[e.model_dump(mode="json") for e in (timeline or [])],
        causal_paths=[],
        graph_topology_json="",
        proposed_action=None,
        rationale=None,
        confidence=0.0,
        alpha_horizon=None,
        risk_assessment=None,
        sequence_id=sequence_id,
        iteration=0,
        analyst_opinions=[],
        debate_result={},
        quant_data="",
    )
