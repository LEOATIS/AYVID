"""Supply Chain Mapper agent — traverses the Neo4j topology to find hidden victims.

This is the first node in the LangGraph DAG. It:
1. Queries Neo4j for causal paths from the shock ticker
2. Feeds the topology + timeline to the LLM for analysis
3. Outputs a target ticker and causal reasoning
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

from shared.config import settings
from shared.logger import get_logger
from graph_intelligence.graph_state.state import CausalArbitrageState
from graph_intelligence.agents.mapper.prompts import MAPPER_SYSTEM, MAPPER_ANALYZE_TOPOLOGY

log = get_logger("agent.mapper")


def _extract_json(text: str) -> str:
    """Strip markdown code fences and extract raw JSON from LLM output."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _get_llm():
    """Get the configured LLM for the mapper agent."""
    if settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
        return ChatAnthropic(
            model=settings.ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=0.1,
            max_tokens=1000,
        )
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.1,
        max_tokens=1000,
    )


async def mapper_node(state: CausalArbitrageState) -> dict:
    """LangGraph node: Analyze supply chain topology and propose a target."""
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
    response = await llm.ainvoke([
        SystemMessage(content=MAPPER_SYSTEM),
        HumanMessage(content=prompt),
    ])

    content = response.content
    try:
        cleaned = _extract_json(content)
        parsed = json.loads(cleaned)
        target = parsed.get("target_ticker", "")
        horizon = parsed.get("alpha_horizon", "3rd_order")
        reasoning = parsed.get("reasoning", content)
        compound_risk = parsed.get("compound_risk", 0.0)
    except (json.JSONDecodeError, TypeError):
        target = state.get("target_ticker", "")
        horizon = "3rd_order"
        reasoning = content
        compound_risk = 0.0

    log.info(f"Mapper proposed target: [bold]{target}[/bold] ({horizon})")

    return {
        "messages": [response],
        "target_ticker": target,
        "alpha_horizon": horizon,
        "rationale": reasoning,
        "confidence": min(compound_risk * 2, 1.0) if compound_risk else 0.5,
        "iteration": state["iteration"] + 1,
    }
