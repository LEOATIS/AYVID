"""Supply Chain Mapper agent — traverses the Neo4j topology to find hidden victims.

This is the first node in the LangGraph DAG. It:
1. Queries Neo4j for causal paths from the shock ticker
2. Feeds the topology + timeline to the LLM for analysis
3. Outputs a target ticker and causal reasoning

Phase-3 (P1-J) uses ``with_structured_output`` bound to
``MapperOutput`` so a malformed LLM response fails at the SDK boundary
rather than propagating garbage values through ``json.loads``.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from graph_intelligence.agents.mapper.prompts import MAPPER_ANALYZE_TOPOLOGY, MAPPER_SYSTEM
from graph_intelligence.agents.mapper.schema import MapperOutput
from graph_intelligence.graph_state.state import CausalArbitrageState
from shared.logger import get_logger

log = get_logger("agent.mapper")


def _get_llm():
    """Build a `MapperOutput`-shaped LLM client (P1-J).

    ``with_structured_output`` forces the LLM through an SDK-side
    validator bound to the Pydantic schema. Malformed / out-of-range
    values raise ``ValidationError`` which we catch below.
    """
    from shared.llm_client import get_langchain_chat_model

    return get_langchain_chat_model(
        temperature=0.1,
        max_tokens=1000,
    ).with_structured_output(MapperOutput)


def _derive_direction_from_intel(
    state: CausalArbitrageState,
    target_ticker: str,
) -> str:
    """Deterministic fallback for the mapper's direction field.

    Gap A6 fix: the mapper's JSON schema now requires a `direction`. If
    the LLM omits it (or returns an unexpected value), we derive it from
    `causal_intel` rather than silently defaulting to LONG.

    Priority:
      1. causal_intel.beneficiary_tickers / victim_tickers membership
      2. causal_intel.index_bias mapping (for index targets)
      3. shock event_type: SHOCK → SHORT, RECOVERY → LONG
      4. HOLD (signals the orchestrator to skip)
    """
    intel = state.get("causal_intel", {}) or {}
    beneficiaries = set(intel.get("beneficiary_tickers", []) or [])
    victims = set(intel.get("victim_tickers", []) or [])
    if target_ticker in beneficiaries:
        return "LONG"
    if target_ticker in victims:
        return "SHORT"

    index_bias = intel.get("index_bias", {}) or {}
    bias = (index_bias.get(target_ticker) or "").upper()
    if bias == "BULLISH":
        return "LONG"
    if bias == "BEARISH":
        return "SHORT"

    # Fall back to the most recent shock's event type. In the N-th order
    # victim thesis, a SHOCK event almost always makes the downstream
    # target a SHORT (supply-chain squeeze on the target's earnings);
    # a RECOVERY event reverses the thesis.
    timeline = state.get("timeline", []) or []
    if timeline:
        latest = timeline[-1]
        event_type = (latest.get("event_type") or "").upper()
        if event_type == "RECOVERY":
            return "LONG"
        if event_type in ("SHOCK", "COMMODITY_SHOCK", "FX_SHOCK", "REGULATORY"):
            return "SHORT"

    return "HOLD"


async def mapper_node(state: CausalArbitrageState) -> dict:
    """LangGraph node: Analyze supply chain topology and propose a target.

    Phase-3: emits `proposed_action` (LONG/SHORT/HOLD) derived from the
    LLM's JSON `direction` field with deterministic fallback to causal
    intel. This closes the A6 leak where missing direction silently
    defaulted to LONG downstream.
    """
    log.info(f"Mapper analyzing shock on [bold]{state['shock_ticker']}[/bold]")

    timeline_str = json.dumps(state["timeline"][:20], indent=2, default=str)
    topology_str = state.get("graph_topology_json", "No topology data available")
    paths_str = json.dumps(state.get("causal_paths", [])[:10], indent=2, default=str)

    prompt = MAPPER_ANALYZE_TOPOLOGY.format(
        shock_ticker=state["shock_ticker"],
        timeline=timeline_str,
        graph_topology=topology_str,
        causal_paths=paths_str,
    )

    llm = _get_llm()
    try:
        parsed: MapperOutput = await llm.ainvoke(
            [
                SystemMessage(content=MAPPER_SYSTEM),
                HumanMessage(content=prompt),
            ]
        )
    except (ValidationError, ValueError, TypeError) as exc:
        log.warning(
            f"Mapper LLM output failed Pydantic validation: {exc}. "
            f"Degrading to HOLD with causal-intel fallback."
        )
        parsed = MapperOutput()
    except Exception as exc:
        log.warning(f"Mapper LLM invocation failed: {exc}")
        parsed = MapperOutput()

    if not isinstance(parsed, MapperOutput):
        parsed = MapperOutput()

    target = parsed.target_ticker or state.get("target_ticker", "")
    horizon = parsed.alpha_horizon
    compound_risk = parsed.compound_risk  # Pydantic already clamps to [0,1]
    raw_direction = parsed.direction

    # Direction contract: LLM must emit LONG / SHORT / HOLD. When HOLD
    # we still run the causal-intel fallback so a defensive default
    # does not silently become a missed trade opportunity: if causal
    # intel disagrees with HOLD, we surface the conflict in the log
    # and defer to the fallback direction. Pure HOLD (with agreeing
    # causal intel) stays HOLD.
    if raw_direction in ("LONG", "SHORT"):
        direction = raw_direction
    else:
        direction = _derive_direction_from_intel(state, target)
        if direction != "HOLD":
            log.warning(
                f"Mapper: LLM said HOLD on {target}; causal intel "
                f"suggests {direction}. Using causal intel."
            )

    log.info(
        f"Mapper proposed: [bold]{direction} {target}[/bold] ({horizon}) — "
        f"compound_risk={compound_risk:.2f}"
    )

    # Record the structured output as a faux AIMessage so downstream
    # consumers that look at `messages` still see a coherent trace.
    from langchain_core.messages import AIMessage

    trace_msg = AIMessage(content=parsed.model_dump_json())

    return {
        "messages": [trace_msg],
        "target_ticker": target,
        "alpha_horizon": horizon,
        "proposed_action": direction,
        "rationale": parsed.reasoning or "(mapper returned no rationale)",
        "confidence": min(compound_risk * 2, 1.0) if compound_risk > 0 else 0.5,
        "iteration": state["iteration"] + 1,
    }
