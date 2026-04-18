"""Trade Auditor agent — stress-tests proposed signals before execution.

Second node in the LangGraph DAG. Challenges the mapper's thesis
and either approves, adjusts confidence, or kills the trade.
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
from graph_intelligence.agents.auditor.prompts import AUDITOR_SYSTEM, AUDITOR_REVIEW

log = get_logger("agent.auditor")


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
    if settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
        return ChatAnthropic(
            model=settings.ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=0.2,
            max_tokens=1000,
        )
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.2,
        max_tokens=1000,
    )


async def auditor_node(state: CausalArbitrageState) -> dict:
    """LangGraph node: Review and stress-test the proposed trade signal."""
    log.info(
        f"Auditor reviewing [bold]{state.get('target_ticker', '?')}[/bold] signal"
    )

    prompt = AUDITOR_REVIEW.format(
        shock_ticker=state["shock_ticker"],
        target_ticker=state.get("target_ticker", "UNKNOWN"),
        alpha_horizon=state.get("alpha_horizon", "unknown"),
        confidence=state.get("confidence", 0.0),
        rationale=state.get("rationale", "No rationale provided"),
        timeline=json.dumps(state["timeline"][:15], indent=2, default=str),
        graph_topology=state.get("graph_topology_json", "No topology"),
    )

    llm = _get_llm()
    response = await llm.ainvoke([
        SystemMessage(content=AUDITOR_SYSTEM),
        HumanMessage(content=prompt),
    ])

    content = response.content
    try:
        cleaned = _extract_json(content)
        parsed = json.loads(cleaned)
        approved = parsed.get("approved", False)
        confidence_adj = parsed.get("confidence_adjustment", 0.0)
        risk_assessment = parsed.get("risk_assessment", content)
        action = parsed.get("proposed_action", "HOLD")
        concerns = parsed.get("concerns", [])
        kill_reasons = parsed.get("kill_reasons", [])
    except (json.JSONDecodeError, TypeError):
        approved = False
        confidence_adj = -0.1
        risk_assessment = content
        action = "HOLD"
        concerns = []
        kill_reasons = []

    new_confidence = max(0.0, min(1.0, state.get("confidence", 0.5) + confidence_adj))

    if kill_reasons:
        action = "HOLD"
        new_confidence = 0.0
        log.warning(f"Auditor KILLED trade: {kill_reasons}")
    elif approved:
        log.info(f"Auditor APPROVED: {action} at confidence {new_confidence:.2f}")
    else:
        action = "HOLD"
        log.info(f"Auditor REJECTED — concerns: {concerns}")

    return {
        "messages": [response],
        "proposed_action": action,
        "confidence": new_confidence,
        "risk_assessment": risk_assessment,
        "iteration": state["iteration"] + 1,
    }
